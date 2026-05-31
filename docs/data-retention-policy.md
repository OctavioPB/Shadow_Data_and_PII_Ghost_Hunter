# Data Retention Policy — PII Ghost-Hunter

**Document type:** Technical & Compliance Policy  
**Owner:** Data Protection Officer  
**Effective date:** 2026-05-17  
**Review cycle:** Annual or after any regulatory change  
**Legal basis:** GDPR Article 5(1)(e) — Storage Limitation; LGPD Article 6(X)

---

## Purpose

This document defines how long PII-related data is retained across all components of the Shadow Data & PII Ghost-Hunter platform. It specifies automated enforcement mechanisms and manual override procedures.

---

## Scope

This policy applies to all data stored by the following system components:

| Component | Storage location | Data type |
|---|---|---|
| Scanner Events | PostgreSQL `scanner_events` | Table/file metadata |
| PII Findings | PostgreSQL `pii_findings` | Column classification results |
| Column Samples | S3 `pii-hunter-staging` | Column value samples (anonymized metadata) |
| Quarantine Zone | S3 `pii-quarantine` | Original flagged data pending DPO decision |
| Audit Log | PostgreSQL `audit_log` | Immutable action records |
| ML Model Artifacts | S3 `pii-hunter-models` | Model weights and evaluation reports |

---

## Retention Schedules

### 1. Quarantined Data — 30 Days

**Location:** `s3://pii-quarantine/pending/{table_id}/`  
**Trigger:** Table quarantined by Patrol DAG or manual DPO action  
**Retention period:** 30 calendar days from quarantine date

**Enforcement (automated):**

| Day | Event |
|---|---|
| 0 | Data moved to quarantine; entry created in `quarantine_manifest` |
| 23 | `dag_quarantine_expiry` sends warning email to DPO and data owner |
| 23 | S3 lifecycle rule transitions objects to GLACIER (signals imminent expiry) |
| 30 | `dag_quarantine_expiry` moves objects to `expired/` prefix |
| 31 | S3 lifecycle rule permanently deletes objects in `expired/` prefix |
| 30 | `quarantine_manifest.expired_at` and `audit_log` entry written |

**Override:** DPO may request a retention extension by contacting the platform team before day 30. Extensions require a legal hold justification and are documented in the audit log.

---

### 2. Column Samples — 7 Days

**Location:** `s3://pii-hunter-staging/samples/{table_id}/`  
**Purpose:** Temporary storage during ML inference pipeline  
**Retention period:** 7 calendar days from creation

**Enforcement (automated):** S3 lifecycle rule on the `samples/` prefix deletes objects after 7 days.

**Note:** Column samples contain anonymized metadata only. Actual data values are never written to the staging bucket.

---

### 3. Scanner Events — 2 Years

**Location:** PostgreSQL `scanner_events` table  
**Retention period:** 24 months from event creation

**Enforcement:** Manual archival job (quarterly). Events older than 24 months are exported to `s3://pii-hunter-datalake/archive/scanner_events/` and deleted from PostgreSQL.

---

### 4. PII Findings — 2 Years

**Location:** PostgreSQL `pii_findings` table  
**Retention period:** 24 months from finding creation

**Exception:** Findings where `status = 'remediated'` are retained for 5 years to support GDPR Article 5(2) accountability documentation.

---

### 5. Audit Log — 5 Years (Immutable)

**Location:** PostgreSQL `audit_log` table  
**Retention period:** 60 months (5 years) from event creation  
**Constraint:** `audit_log` is **append-only** — no UPDATE or DELETE operations are permitted. Enforced at the database level via a trigger.

**Rationale:** GDPR Article 30 (Records of Processing Activities) and LGPD Article 37 require evidence of compliance decisions to be available for regulatory inspection.

---

### 6. ML Model Artifacts — Indefinite (versioned)

**Location:** `s3://pii-hunter-models/{version}/`  
**Retention period:** Indefinite; S3 versioning enabled  
**Deprecation:** Old model versions are marked `deprecated` in the `model_registry` table. Deprecated artifacts are retained for 1 year then archived to GLACIER.

---

## Automated Enforcement Summary

| Rule | Mechanism | Monitored by |
|---|---|---|
| Quarantine 30-day expiry | `dag_quarantine_expiry` + S3 lifecycle | Platform alerts |
| Quarantine 23-day warning | `dag_quarantine_expiry` email | DPO inbox |
| Staging samples 7-day delete | S3 lifecycle rule | Platform alerts |
| Audit log append-only | PostgreSQL trigger | Security audit |

If the `dag_quarantine_expiry` DAG fails, the S3 lifecycle rule on the `expired/` prefix acts as a safety net for the final deletion. However, the warning email will not be sent — monitor the DAG failure alert in PagerDuty.

---

## DPO Responsibilities

1. **Review** quarantine expiry warnings within 7 days of receipt
2. **Decide** to delete (no action), export (contact platform team), or extend (legal hold required)
3. **Confirm** the data retention policy annually with Legal & Privacy during the review cycle
4. **Report** any accidental early deletion as a potential data breach per GDPR Article 33

---

## Exception Process

To request a retention extension beyond the standard 30-day quarantine window:

1. Submit a written request to the platform team with legal hold justification
2. Platform team applies a `legal_hold = true` flag in `quarantine_manifest`
3. `dag_quarantine_expiry` will skip entries with `legal_hold = true`
4. Extension duration is determined by the legal team; documented in the audit log

---

## Regulatory References

| Regulation | Article | Requirement |
|---|---|---|
| GDPR | Art. 5(1)(e) | Storage limitation — data kept no longer than necessary |
| GDPR | Art. 17 | Right to erasure — deleted within regulatory timeframe |
| GDPR | Art. 30 | Records of processing activities — 5-year audit trail |
| LGPD | Art. 6(X) | Non-retention — eliminate data when purpose ends |
| LGPD | Art. 37 | Record keeping obligations for controllers |

---

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-05-17 | Initial policy — Sprint 8 production rollout | Platform Engineering |
