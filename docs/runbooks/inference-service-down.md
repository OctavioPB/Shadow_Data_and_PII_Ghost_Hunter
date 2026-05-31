# Runbook: Inference Service Down

**Service:** `pii-inference` (FastAPI, port 8001)  
**On-call severity:** P1 (PII classification blocked — no remediation possible)  
**SLA impact:** Tables cannot be classified; remediation queue grows unbounded.

---

## Symptoms

- `GET http://pii-inference:8001/health` returns non-200 or times out
- Alert fires: `pii_inference_service_down` (PagerDuty)
- Prometheus metric `inference_requests_total` flat for > 5 minutes
- Airflow `PIIClassifierOperator` tasks failing with `RuntimeError: Inference API returned 503`
- Dashboard shows no new findings — existing backlog not processed

---

## Diagnosis

### Step 1 — Check pod/container health

```bash
# Kubernetes
kubectl get pods -n pii-ghost-hunter -l app=pii-inference
kubectl describe pod <pod-name> -n pii-ghost-hunter
kubectl logs <pod-name> -n pii-ghost-hunter --tail=100

# Docker Compose (local)
docker compose ps pii-inference
docker compose logs pii-inference --tail=100
```

### Step 2 — Check the health endpoint

```bash
curl -v http://pii-inference:8001/health
# Expected: {"status":"ok","model_loaded":true,"version":"..."}
```

If `model_loaded: false`: the model failed to load from S3. See Case B.

### Step 3 — Check model artifact availability

```bash
aws s3 ls s3://pii-hunter-models/ --recursive | grep model.tar.gz
# Verify the latest version artifact exists and is non-zero size
```

### Step 4 — Check resource limits

```bash
kubectl top pods -n pii-ghost-hunter
# Inference service is CPU/memory intensive — OOMKill is a common cause
kubectl get events -n pii-ghost-hunter --field-selector reason=OOMKilling
```

---

## Resolution

### Case A — Pod crashed / OOMKilled

```bash
# Restart the deployment
kubectl rollout restart deployment/pii-inference -n pii-ghost-hunter

# Monitor rollout
kubectl rollout status deployment/pii-inference -n pii-ghost-hunter

# If repeated OOMKills, increase memory limit in Helm values:
helm upgrade pii-ghost-hunter ./infra/helm \
  --set inference.resources.limits.memory=4Gi
```

### Case B — Model failed to load from S3

```bash
# Verify S3 URI in the pod environment
kubectl exec -n pii-ghost-hunter <pod> -- env | grep MODEL_S3_PATH

# Check the model artifact is accessible from the pod's IAM role
kubectl exec -n pii-ghost-hunter <pod> -- \
  aws s3 cp $MODEL_S3_PATH /tmp/model.tar.gz && echo "S3 accessible"

# If IAM role issue, check the pod's service account annotation:
kubectl describe sa pii-inference-sa -n pii-ghost-hunter
```

**If the model artifact is missing or corrupt:**
1. Find the last approved model version in the DB:
   ```sql
   SELECT version, s3_uri FROM model_registry
   WHERE status = 'approved' ORDER BY trained_at DESC LIMIT 1;
   ```
2. Update `MODEL_S3_PATH` in the Helm values to the known-good URI.
3. Restart the deployment.

### Case C — Inference too slow (not down, but SLA breached)

If `inference_latency_p95 > 2s`:

```bash
# Check GPU/CPU utilisation
kubectl top pods -n pii-ghost-hunter

# Reduce batch size or max concurrent requests:
helm upgrade pii-ghost-hunter ./infra/helm \
  --set inference.env.MAX_BATCH_SIZE=5
```

Scale out replicas:
```bash
kubectl scale deployment pii-inference --replicas=3 -n pii-ghost-hunter
```

### Case D — Service reachable but model not loaded (cold start)

The model is loaded eagerly at startup. If the first request arrives before the model is
ready, subsequent requests block. Wait 60 seconds after pod restart for model load to complete,
then verify:

```bash
curl http://pii-inference:8001/health
# Wait for model_loaded: true
```

---

## Degraded Mode

While the inference service is down, the sampling pipeline will queue tables but not classify
them. Tasks fail with `RuntimeError` and are retried by Airflow (max 3 retries, 5 min backoff).

To prevent queue buildup during extended outages:
```bash
# Pause the sampling DAG until inference is restored
airflow dags pause dag_sampling_pipeline
```

Unpause after recovery:
```bash
airflow dags unpause dag_sampling_pipeline
# Manually trigger to process the backlog
airflow dags trigger dag_sampling_pipeline
```

---

## Escalation

After 15 minutes of P1:
1. Page the ML engineering team.
2. If no approved model artifact is available, temporarily set `MODEL_CONFIDENCE_THRESHOLD=1.0`
   to stop all auto-classification (prevents false positives).
3. Notify the DPO that classification is paused.

---

## Post-Incident

1. Verify `inference_requests_total` resumes normal rate.
2. Confirm queued tables are classified (check `column_samples.status` transitions).
3. Add a pre-flight health check to the `PIIClassifierOperator` to detect outages faster.
4. Review if memory limits need permanent increase.
