# Runbook: Inference Service Blue/Green Rollout & Rollback

**Applies to:** PII Inference service (`pii-inference-bluegreen` Helm chart)  
**Owner:** Platform Engineering  
**SLA:** Zero-downtime model updates; rollback in < 5 minutes if SLA breach detected

---

## Overview

The inference service uses a blue/green deployment strategy. Both slots run concurrently; the production `pii-inference` Service selects one via a `slot:` label. Shifting traffic is a single Helm upgrade with `--set activeSlot=<slot>` — no pod restarts, no downtime.

```
pii-inference (Service)  ──► slot: blue   (Deployment: pii-inference-blue)
                              slot: green  (Deployment: pii-inference-green)
```

---

## Pre-conditions

- `kubectl` configured for the target cluster (`aws eks update-kubeconfig ...`)
- `helm` ≥ 3.14 installed
- New image tag built and pushed to ECR
- You know the current active slot: `kubectl get svc pii-inference -n pii-ghost-hunter -o jsonpath='{.metadata.annotations.deployment/active-slot}'`

---

## Rollout Procedure

### Step 1 — Identify active and inactive slots

```bash
ACTIVE=$(kubectl get svc pii-inference -n pii-ghost-hunter \
  -o jsonpath='{.metadata.annotations.deployment/active-slot}')
INACTIVE=$([ "$ACTIVE" = "blue" ] && echo "green" || echo "blue")
echo "Active: $ACTIVE | Inactive: $INACTIVE"
```

### Step 2 — Deploy new image to the INACTIVE slot

```bash
# Example: current active=blue, deploying new model to green
helm upgrade pii-inference-bluegreen infra/helm/pii-inference-bluegreen \
  --namespace pii-ghost-hunter \
  --reuse-values \
  --set green.image.tag=<NEW_SHA> \
  --set green.replicaCount=3       # scale to match blue before cutover
  --wait --timeout 5m
```

Wait for green pods to become Ready:

```bash
kubectl rollout status deployment/pii-inference-green -n pii-ghost-hunter
```

### Step 3 — Smoke-test the INACTIVE slot via preview Service

```bash
# Port-forward the preview service (no production traffic)
kubectl port-forward svc/pii-inference-green-preview 18001:8001 -n pii-ghost-hunter &

# Health check
curl -sf http://localhost:18001/health

# Classification smoke test
curl -sf http://localhost:18001/infer \
  -H 'Content-Type: application/json' \
  -d '{"table_id":"smoke-test","columns":[{"column_id":"c1","column_name":"email_address","values":["alice@example.com"]}]}'
# Expected: pii_category=EMAIL, confidence >= 0.85

kill %1
```

### Step 4 — Shift production traffic to the INACTIVE slot

```bash
helm upgrade pii-inference-bluegreen infra/helm/pii-inference-bluegreen \
  --namespace pii-ghost-hunter \
  --reuse-values \
  --set activeSlot=$INACTIVE \
  --wait --timeout 2m
```

Verify:

```bash
kubectl get svc pii-inference -n pii-ghost-hunter \
  -o jsonpath='{.metadata.annotations.deployment/active-slot}'
# Should print: green
```

### Step 5 — Monitor SLAs for 10 minutes

Watch Grafana: **PII Ghost-Hunter — Production Overview** → "Inference — p95 Latency" panel.

Target: p95 < 2000ms. If breached → proceed to **Rollback Procedure** below.

```bash
# Quick CLI check: watch error rate on inference
watch -n 10 'kubectl logs -l app.kubernetes.io/name=pii-inference,slot=$INACTIVE \
  -n pii-ghost-hunter --tail=20 | grep -c ERROR'
```

### Step 6 — Scale down the now-INACTIVE slot

```bash
helm upgrade pii-inference-bluegreen infra/helm/pii-inference-bluegreen \
  --namespace pii-ghost-hunter \
  --reuse-values \
  --set ${ACTIVE}.replicaCount=1   # warm standby only
```

---

## Rollback Procedure

**If SLA breach detected within 10-minute monitoring window:**

```bash
# Shift traffic back to the previously active slot
helm upgrade pii-inference-bluegreen infra/helm/pii-inference-bluegreen \
  --namespace pii-ghost-hunter \
  --reuse-values \
  --set activeSlot=$ACTIVE \
  --wait --timeout 2m
```

Traffic reverts in < 30 seconds (no pod restarts — only the Service selector changes).

Verify rollback:

```bash
kubectl get svc pii-inference -n pii-ghost-hunter \
  -o jsonpath='{.metadata.annotations.deployment/active-slot}'
```

**Expected RTO: < 5 minutes from SLA breach detection to rollback complete.**

---

## Escalation

| Condition | Action |
|---|---|
| Rollback does not resolve latency | Scale up the now-active slot: `--set blue.replicaCount=6` |
| Both slots failing health checks | Check model S3 availability: `aws s3 ls s3://pii-hunter-models/` |
| All pods CrashLooping | Check `kubectl logs` for OOM; increase memory limits in values |
| Smoke test fails for both slots | Roll back the model artifact in S3; redeploy previous tag |

Escalate to ML Engineering if inference health checks fail after rollback.

---

## Post-Rollout Checklist

- [ ] p95 latency < 2000ms for 10 minutes post-cutover
- [ ] Error rate < 5% on `/infer` endpoint
- [ ] Old slot scaled down to 1 replica
- [ ] Deployment SHA and model version recorded in the team's deploy log
- [ ] Grafana snapshot saved for this release
