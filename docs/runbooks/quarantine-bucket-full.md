# Runbook: Quarantine Bucket Full

**Bucket:** `s3://pii-quarantine/`  
**On-call severity:** P2 (new tables cannot be quarantined; existing data safe)  
**SLA impact:** Quarantine jobs fail; flagged tables remain in staging S3.

---

## Symptoms

- Alert fires: `pii_quarantine_bucket_usage > 85%` (CloudWatch alarm)
- `etl/quarantine/quarantine_job.py` raises `ClientError: Bucket quota exceeded`
- Airflow `quarantine_raw_data` task fails with `OSError: S3 write failed`
- Dashboard shows tables stuck in `flagged` status with no quarantine progress

---

## Prevention

The quarantine bucket has an S3 lifecycle policy (configured in `infra/terraform/s3.tf`):

```hcl
rule {
  id     = "quarantine-expiry"
  status = "Enabled"
  filter { prefix = "pending/" }
  expiration { days = 30 }
}
```

Data that is not reviewed within 30 days is automatically deleted.
The `dag_quarantine_expiry.py` DAG notifies the DPO 7 days before any expiry.

If the bucket fills before the 30-day window, it indicates either:
1. A large volume of PII was detected in a short period (legitimate).
2. The lifecycle policy was accidentally disabled.
3. DPO review is significantly behind — too much data pending.

---

## Diagnosis

### Step 1 — Measure bucket usage

```bash
aws s3 ls s3://pii-quarantine/ --recursive --human-readable --summarize 2>&1 | tail -3
# Shows: Total Objects / Total Size

# CloudWatch metric
aws cloudwatch get-metric-statistics \
  --namespace AWS/S3 \
  --metric-name BucketSizeBytes \
  --dimensions Name=BucketName,Value=pii-quarantine \
  --start-time $(date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 86400 \
  --statistics Average
```

### Step 2 — Identify the largest objects

```bash
aws s3 ls s3://pii-quarantine/pending/ --recursive --human-readable \
  | sort -h -k3 | tail -20
```

### Step 3 — Check lifecycle policy status

```bash
aws s3api get-bucket-lifecycle-configuration --bucket pii-quarantine
# Verify the quarantine-expiry rule is present and "Enabled"
```

### Step 4 — Identify stale (unreviewed) items

```sql
SELECT table_id, source_s3_path, file_count, total_bytes, quarantined_at,
       EXTRACT(DAYS FROM now() - quarantined_at) AS days_pending
FROM quarantine_manifest
WHERE status = 'quarantined'
  AND reviewed_at IS NULL
ORDER BY quarantined_at ASC;
```

---

## Resolution

### Case A — Lifecycle policy disabled

```bash
# Re-apply the lifecycle policy
aws s3api put-bucket-lifecycle-configuration \
  --bucket pii-quarantine \
  --lifecycle-configuration file://infra/terraform/s3-lifecycle.json

# Verify
aws s3api get-bucket-lifecycle-configuration --bucket pii-quarantine
```

### Case B — DPO review backlog (most common)

Items older than 30 days are auto-expired. For immediate relief:

1. **Identify and release false positives** (DPO action via dashboard):
   - Filter quarantine_manifest by `reviewed_at IS NULL AND quarantined_at < NOW() - INTERVAL '7 days'`
   - DPO reviews tables in the dashboard and marks as `false_positive`

2. **Manually expire long-pending items** (requires DPO approval):
   ```bash
   # List items pending > 20 days
   aws s3 ls s3://pii-quarantine/pending/ --recursive \
     | awk '$1 < "2026-04-26"' | head -20
   
   # Remove with DPO written approval in the audit log
   aws s3 rm s3://pii-quarantine/pending/<table_id>/ --recursive
   ```
   After deletion, update the DB:
   ```sql
   UPDATE quarantine_manifest
   SET status = 'released', released_at = NOW(), notes = 'Expired — DPO approved'
   WHERE table_id = '<table_id>';
   ```

3. **Request a bucket quota increase** from AWS Support if legitimate growth requires it.

### Case C — Burst of large tables detected

If a data migration or ETL job accidentally created many PII-containing tables:

1. Pause the patrol DAG to stop generating new quarantine candidates:
   ```bash
   airflow dags pause dag_patrol_new_tables
   ```

2. Identify the source of the burst:
   ```sql
   SELECT source_name, count(*) AS table_count, sum(total_bytes) / 1e9 AS total_gb
   FROM quarantine_manifest
   WHERE quarantined_at > NOW() - INTERVAL '24 hours'
   GROUP BY source_name ORDER BY total_gb DESC;
   ```

3. If the source is a known safe environment (e.g., a dev dataset), bulk-release via DPO approval.

4. Unpause the DAG after the burst is resolved.

---

## Escalation

If bucket usage exceeds 95%:
1. Page the DPO immediately (P1 escalation).
2. Pause `dag_remediation` to stop new quarantine operations.
3. Request emergency quota increase from AWS Support.

---

## Post-Incident

1. Verify lifecycle policy is active.
2. Set up a CloudWatch alarm at 70% usage (earlier warning threshold).
3. Schedule a quarterly DPO review session for backlogged quarantine items.
4. If the burst was caused by a mis-classified data source, add it to the patrol exclusion list.
