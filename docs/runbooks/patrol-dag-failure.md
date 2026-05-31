# Runbook: Patrol DAG Failure

**DAG:** `dag_patrol_new_tables`  
**Schedule:** Daily 00:00 UTC  
**On-call severity:** P2 (detection gap, no data loss)  
**SLA impact:** Ghost tables may go undetected until next successful run.

---

## Symptoms

- Airflow UI shows DAG run in **Failed** or **Zombie** state
- Alert fires: `pii_patrol_dag_failure` (PagerDuty)
- Kafka topic `pii.candidates` has no new messages for > 26 hours
- Dashboard Risk Inventory shows no new tables added since previous scan

---

## Diagnosis

### Step 1 — Identify the failing task

```bash
# View recent DAG run logs
airflow dags list-runs -d dag_patrol_new_tables --state failed

# Get task-level failure detail
airflow tasks logs dag_patrol_new_tables <task_id> <run_id>
```

**Common failing tasks:**

| Task | Common cause |
|---|---|
| `query_new_tables` | PostgreSQL connection failure or credentials expired |
| `enqueue_to_kafka` | Kafka broker unreachable or SSL cert expired |
| `update_patrol_cursor` | Optimistic lock conflict (concurrent DAG run) |

### Step 2 — Check downstream services

```bash
# PostgreSQL reachability
psql $DATABASE_URL -c "SELECT 1"

# Kafka broker health
kafka-broker-api-versions.sh --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS

# Airflow scheduler health
airflow jobs check --job-type SchedulerJob --limit 1
```

### Step 3 — Check cursor state (idempotency)

```sql
-- Confirm the patrol cursor was not corrupted
SELECT * FROM audit_log
WHERE event_type = 'patrol_cursor_updated'
ORDER BY timestamp DESC
LIMIT 5;
```

---

## Resolution

### Case A — Transient network failure

The DAG is idempotent. Simply re-trigger:

```bash
airflow dags trigger dag_patrol_new_tables
```

### Case B — PostgreSQL connection failure

1. Verify `DATABASE_URL` in Airflow connections matches current RDS endpoint.
2. If RDS failover occurred, update the connection string:
   ```bash
   airflow connections set pii_postgres --conn-uri postgresql://user:pass@new-host/pii_gh
   ```
3. Re-trigger the DAG.

### Case C — Kafka SSL certificate expired

1. Rotate certificates via AWS ACM or the MSK console.
2. Update the `KAFKA_SSL_CAFILE` Airflow Variable.
3. Restart the Airflow scheduler pod: `kubectl rollout restart deployment/airflow-scheduler`
4. Re-trigger the DAG.

### Case D — Zombie task (stuck > 30 min)

```bash
# Kill the zombie task
airflow tasks clear dag_patrol_new_tables -t <task_id> -s <run_id> --yes

# Re-trigger
airflow dags trigger dag_patrol_new_tables
```

### Case E — Cursor corruption (duplicate scans)

The `query_new_tables` task uses `WHERE created_at > :last_scanned` with idempotency
enforced by the `scanner_events.event_id` unique constraint. Even if the cursor rolls back
to an earlier timestamp, duplicate events will be silently ignored (ON CONFLICT DO NOTHING).

---

## Escalation

If the DAG fails for > 3 consecutive days:
1. Open a P1 incident.
2. Verify the Airflow scheduler is running and has sufficient resources.
3. Check for PostgreSQL replication lag > 30s (may cause stale reads).
4. Contact the data platform team.

---

## Post-Incident

After resolving:
1. Verify the next scheduled run completes successfully.
2. Confirm new scanner events appear in the Risk Inventory dashboard.
3. If the gap was > 48 hours, manually trigger `dag_sampling_pipeline` for any tables
   created during the outage window.
4. Record the incident in the post-mortem doc.
