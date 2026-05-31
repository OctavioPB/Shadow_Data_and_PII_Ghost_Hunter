# Runbook: Kafka Consumer Lag Spike

**Consumer group:** `pii-scanner-group`  
**Topics monitored:** `table.created`, `file.moved`, `schema.changed`, `pii.candidates`  
**On-call severity:** P2 (detection delay) → P1 if lag > 24h SLA  
**SLA impact:** Ghost tables may be detected late; compliance window at risk.

---

## Thresholds

| Metric | Warning | Critical |
|---|---|---|
| Consumer lag (messages behind) | > 1,000 | > 10,000 |
| Lag growth rate | > 50 msg/min | > 500 msg/min |
| Time-to-consume (estimated) | > 1 hour | > 24 hours |

---

## Symptoms

- Prometheus alert: `kafka_consumer_lag > 10000` for group `pii-scanner-group`
- PagerDuty fires: `pii_kafka_consumer_lag_critical`
- Dashboard: Risk Inventory shows no new tables despite known data lake activity
- Airflow DAG `dag_patrol_new_tables` produces 0 new tables when source tables exist

---

## Diagnosis

### Step 1 — Measure current lag

```bash
# Confluent tooling
kafka-consumer-groups.sh \
  --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
  --describe \
  --group pii-scanner-group

# Output shows: TOPIC | PARTITION | CURRENT-OFFSET | LOG-END-OFFSET | LAG
```

### Step 2 — Identify which topic has the spike

```bash
kafka-consumer-groups.sh \
  --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
  --describe \
  --group pii-scanner-group \
  | awk '$6 > 1000 {print $1, $2, $6}'
# Prints: TOPIC PARTITION LAG for partitions with lag > 1000
```

### Step 3 — Check consumer pod health

```bash
# Are the scanner consumers running?
kubectl get pods -n pii-ghost-hunter -l app=pii-scanner
kubectl logs -n pii-ghost-hunter -l app=pii-scanner --tail=100

# Is the consumer committing offsets?
# A healthy consumer shows "committed offset X" in logs
```

### Step 4 — Check message production rate vs. consumption rate

```bash
# Prometheus query (Grafana)
# Producer rate:
rate(kafka_server_brokertopicmetrics_messagesinpersec[5m]){topic="table.created"}
# Consumer rate:
rate(kafka_consumer_fetch_manager_records_consumed_rate[5m]){topic="table.created"}
```

A lag spike with normal consumption rate = producer burst.
A lag spike with zero consumption rate = consumer is dead.

### Step 5 — Check for processing errors (poison pill messages)

```bash
# Scanner logs — look for deserialization errors
kubectl logs -n pii-ghost-hunter -l app=pii-scanner | grep -i "error\|exception\|failed"
```

---

## Resolution

### Case A — Consumer pod(s) crashed

```bash
kubectl rollout restart deployment/pii-scanner -n pii-ghost-hunter
kubectl rollout status deployment/pii-scanner -n pii-ghost-hunter

# Monitor lag recovery
watch -n 10 'kafka-consumer-groups.sh \
  --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
  --describe --group pii-scanner-group | grep -v NONE'
```

### Case B — Producer burst (legitimate or accidental)

If a large data migration created thousands of tables:

1. Check if this is expected:
   ```bash
   kafka-run-class.sh kafka.tools.GetOffsetShell \
     --broker-list $KAFKA_BOOTSTRAP_SERVERS \
     --topic table.created --time -1
   ```

2. Scale out consumers to process the backlog:
   ```bash
   kubectl scale deployment/pii-scanner --replicas=6 -n pii-ghost-hunter
   # Note: max replicas = number of partitions in the topic (default: 6)
   ```

3. Scale back to normal after lag is cleared:
   ```bash
   kubectl scale deployment/pii-scanner --replicas=2 -n pii-ghost-hunter
   ```

### Case C — Poison pill message (consumer stuck on one message)

If the consumer keeps restarting and lag doesn't decrease:

```bash
# Find which offset the consumer is stuck on
kafka-consumer-groups.sh \
  --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
  --describe --group pii-scanner-group \
  | awk '{print $1, $2, $3}'

# Skip the bad message by advancing the offset +1
kafka-consumer-groups.sh \
  --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS \
  --group pii-scanner-group \
  --topic table.created \
  --reset-offsets --to-offset <stuck_offset+1> \
  --partition <partition_number> \
  --execute
```

**Warning:** Skipping offsets drops events. Log the skipped offset in the audit log:
```sql
INSERT INTO audit_log (event_type, actor, details_json)
VALUES (
  'kafka_offset_skipped',
  'on-call-engineer',
  '{"topic":"table.created","partition":0,"offset":12345,"reason":"poison pill — schema validation failure"}'
);
```

### Case D — Kafka broker performance degradation

If all consumer groups show lag simultaneously:

```bash
# Check broker metrics
kafka-broker-api-versions.sh --bootstrap-server $KAFKA_BOOTSTRAP_SERVERS

# Check disk usage on brokers (MSK CloudWatch)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Kafka \
  --metric-name KafkaDataLogsDiskUsed \
  --dimensions Name=Cluster Name,Value=pii-ghost-hunter-msk \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 --statistics Maximum
```

Escalate to AWS Support if broker storage > 80%.

---

## Prevention Tuning

If lag spikes are frequent, adjust consumer configuration:

```python
# scanner/config.py — increase batch size and fetch size
KAFKA_MAX_POLL_RECORDS = 500       # default 500
KAFKA_FETCH_MAX_BYTES = 52428800   # 50 MB
KAFKA_SESSION_TIMEOUT_MS = 45000   # 45s — prevents rebalance under load
```

---

## Escalation

- Lag > 10,000 and not recovering after 30 min: P1
- Estimated catch-up time > 24 hours: notify DPO (detection SLA breach)
- Broker disk > 80%: immediate AWS Support ticket

---

## Post-Incident

1. Document root cause (burst, crash, poison pill, broker issue).
2. If consumer restarted: verify no events were lost (check CURRENT-OFFSET vs. producer offset).
3. If events were skipped: ensure the affected table IDs are manually enqueued:
   ```bash
   airflow dags trigger dag_patrol_new_tables \
     --conf '{"force_rescan_from":"2026-05-01T00:00:00Z"}'
   ```
4. Review lag alert thresholds — adjust warning/critical based on observed normal range.
