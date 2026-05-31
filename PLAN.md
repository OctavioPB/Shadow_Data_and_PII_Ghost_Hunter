# PLAN.md — Shadow Data & PII Ghost-Hunter
## Sprint Roadmap

> **Methodology:** 2-week sprints | **Velocity target:** 40 story points/sprint  
> **Definition of Done:** Code reviewed, tests passing (≥80% coverage), deployed to staging, documented.  
> **Backlog grooming:** Every Friday before sprint start.

---

## 📍 Milestones at a Glance

| Milestone | Sprint | Target Date | Description |
|---|---|---|---|
| M0 | Sprint 0 | Week 1 | Skeleton repo + local dev env |
| M1 | Sprint 1–2 | Week 5 | Kafka scanner + Airflow patrol running |
| M2 | Sprint 3–4 | Week 9 | ML model trained + inference API live |
| M3 | Sprint 5 | Week 11 | Remediation engine (anonymize + quarantine) |
| M4 | Sprint 6 | Week 13 | Dashboard MVP (Privacy Risk Inventory) |
| M5 | Sprint 7 | Week 15 | Hardening, security audit, staging sign-off |
| M6 | Sprint 8 | Week 17 | Production rollout + DPO onboarding |

---

## Sprint 0 — Foundation & Developer Environment
**Duration:** 1 week | **Points:** N/A (setup sprint)

### Goals
Get every developer to `docker compose up` and see all services healthy.

### Tasks

- [ ] Initialize monorepo structure as defined in `CLAUDE.md`
- [ ] Create `docker-compose.yml` with: Kafka + Zookeeper, Airflow (webserver + scheduler), PostgreSQL, Redis, FastAPI stub, React dev server
- [ ] Write `.env.example` with all required environment variables
- [ ] Configure `ruff`, `black`, `pre-commit` hooks for Python
- [ ] Configure `tsc`, `eslint`, `prettier` for TypeScript
- [ ] Set up GitHub Actions: lint + test workflow skeleton
- [ ] Create `BRAND.md` with initial design tokens (colors, typography, iconography, tone)
- [ ] Write `Makefile` with targets: `make dev`, `make test`, `make lint`, `make build`
- [ ] Set up Terraform backend (S3 + DynamoDB lock) for `infra/`
- [ ] Document local setup in `docs/getting-started.md`

### Exit Criteria
`make dev` brings up all containers. `make lint` passes on empty stubs. `BRAND.md` approved by product.

---

## Sprint 1 — Kafka Metadata Scanner
**Duration:** 2 weeks | **Points:** 38

### Goals
A running Kafka consumer that detects new table/file creation events and publishes enriched PII candidates.

### User Stories

**[S1-01] · 8pts**  
_As a platform engineer, I want a Kafka consumer that reads `table.created` events so that new tables are automatically enrolled in the patrol queue._
- Implement `scanner/consumers/table_created_consumer.py`
- Pydantic schema for `TableCreatedEvent`
- Unit tests with mock Kafka messages
- Dead-letter topic for malformed events

**[S1-02] · 8pts**  
_As a platform engineer, I want a Kafka consumer for `file.moved` events on S3 so that new data lake uploads are tracked._
- Implement `scanner/consumers/file_moved_consumer.py`
- Parse S3 event notifications (AWS EventBridge → Kafka)
- Extract: bucket, prefix, estimated row count, file format

**[S1-03] · 5pts**  
_As a platform engineer, I want all scanner events persisted to PostgreSQL so that Airflow can query them._
- `scanner_events` table migration (Alembic)
- Upsert logic (idempotent on `event_id`)
- Index on `created_at`, `status`

**[S1-04] · 8pts**  
_As a platform engineer, I want a Kafka producer that publishes enriched events to `pii.candidates` topic so that downstream consumers can begin processing._
- Enrich event with: data source type, estimated column count, owner metadata
- Avro schema registered in Schema Registry
- Producer with retry + idempotency config

**[S1-05] · 5pts**  
_As a developer, I want integration tests for the scanner using Testcontainers so that CI validates end-to-end event flow._
- Spin up real Kafka container in test
- Assert event flows: input topic → DB persist → output topic

**[S1-06] · 4pts**  
_As an operator, I want a Grafana dashboard for scanner lag and error rate so that I can monitor the health of the ingestion pipeline._
- Prometheus metrics: events consumed/sec, DLQ count, processing latency
- Grafana dashboard JSON committed to `infra/grafana/`

---

## Sprint 2 — Airflow Patrol DAGs & Sampling
**Duration:** 2 weeks | **Points:** 40

### Goals
Airflow patrols new tables every 24 hours and extracts column samples ready for ML inference.

### User Stories

**[S2-01] · 10pts**  
_As a data governance engineer, I want a DAG that queries all tables created in the last 24 hours and enqueues them for sampling so that no new table escapes inspection._
- `dag_patrol_new_tables.py`: Schedule `@daily`
- Reads from `scanner_events` where `status = 'pending'` and `created_at >= now() - 24h`
- Creates one `SamplingTask` per table
- Updates event status to `queued`
- Idempotent: re-running does not double-enqueue

**[S2-02] · 13pts**  
_As a data governance engineer, I want a sampling DAG that pulls up to 1,000 random rows per suspicious column so that the ML model has representative data to classify._
- `dag_sampling_pipeline.py`: Triggered by patrol DAG via `TriggerDagRunOperator`
- Supports sources: AWS Athena, Glue, S3 (Parquet/CSV/JSON)
- Sampling logic: stratified random sample, max 1,000 rows
- Output written to `s3://pii-hunter-staging/samples/{table_id}/` as Parquet
- Column metadata stored in `column_samples` table

**[S2-03] · 8pts**  
_As a developer, I want the sampling jobs to never modify source tables so that the patrol is non-invasive and auditable._
- Read-only IAM policy enforced at Airflow connection level
- Test: attempt write to source — assert `PermissionDenied`
- Sampling operations logged to `audit_log` table (append-only)

**[S2-04] · 5pts**  
_As an operator, I want Airflow DAG run metrics exported to Prometheus so that I can alert on patrol failures._
- DAG success/failure counters
- Sampling duration histogram
- Alert rule: patrol DAG fails 2 consecutive runs

**[S2-05] · 4pts**  
_As a developer, I want a fixture-based integration test suite for both DAGs so that CI validates the patrol-to-sample pipeline end-to-end._

---

## Sprint 3 — ML Model: Training & Evaluation
**Duration:** 2 weeks | **Points:** 42

### Goals
A trained, evaluated, and versioned PII classification model ready for inference.

### User Stories

**[S3-01] · 8pts**  
_As an ML engineer, I want a labeled training dataset of column samples so that the model has ground truth to learn from._
- Synthetic data generator: 10,000+ samples per PII category
- Categories: `SSN`, `CREDIT_CARD`, `EMAIL`, `PHONE`, `FULL_NAME`, `DATE_OF_BIRTH`, `ADDRESS`, `BANK_ACCOUNT`, `PASSPORT`, `NONE`
- Multi-language: Spanish, Portuguese, English
- Store in `ml/data/labeled/` (not committed — S3 backed)

**[S3-02] · 13pts**  
_As an ML engineer, I want to fine-tune `distilbert-base-multilingual-cased` on the PII dataset so that the model classifies obfuscated column values with high accuracy._
- Training script: `ml/training/train.py`
- HuggingFace Trainer API
- Input: up to 1,000 column values concatenated with column name as context
- Evaluation metrics: precision, recall, F1 per class — target F1 ≥ 0.90
- Experiment tracking: MLflow (self-hosted)

**[S3-03] · 8pts**  
_As an ML engineer, I want a model evaluation pipeline that tests against a held-out PII fixture set so that regressions are caught automatically._
- Fixtures: known patterns (VISA cards, Brazilian CPF, SSN, CURP)
- Threshold evaluation: confirm 0.85 confidence works across categories
- Evaluation report auto-generated as JSON + committed to `ml/reports/`

**[S3-04] · 8pts**  
_As an ML engineer, I want model artifacts versioned and pushed to S3 so that inference services can pull the latest approved model._
- Model packaging: HuggingFace `save_pretrained` → tarball
- S3 path: `s3://pii-hunter-models/{version}/model.tar.gz`
- `model_registry` table: version, metrics, status (`candidate`/`approved`/`deprecated`)

**[S3-05] · 5pts**  
_As an operator, I want model training triggered by a CI job so that retraining on new data is reproducible._
- GitHub Actions workflow: `train.yml` (manual trigger + weekly schedule)
- Auto-posts evaluation report as PR comment

---

## Sprint 4 — ML Inference Service & Airflow Integration
**Duration:** 2 weeks | **Points:** 38

### Goals
The model runs as a service; Airflow calls it for every sampled table and stores results.

### User Stories

**[S4-01] · 10pts**  
_As a platform engineer, I want a FastAPI inference microservice that classifies column samples on demand so that Airflow can trigger PII detection as a DAG step._
- `POST /infer` — accepts column samples JSON, returns classifications + confidence scores
- Model loaded at startup from S3 (lazy-load with cache)
- Batch inference: process up to 50 columns per request
- Response schema: `{ column_id, pii_category, confidence, flagged: bool }`

**[S4-02] · 8pts**  
_As a data governance engineer, I want an Airflow operator that calls the inference service and persists results so that classification outputs are queryable._
- Custom `PIIClassifierOperator`
- Reads samples from S3, calls inference API, writes to `pii_findings` table
- Status transitions: `sampled` → `classified` → `flagged` / `clean`

**[S4-03] · 8pts**  
_As a data governance engineer, I want the patrol DAG to automatically trigger remediation when `confidence >= 0.85` so that no human intervention is needed for high-confidence PII._
- Extend `dag_patrol_new_tables.py` with branch operator
- High-confidence path → trigger `dag_remediation.py`
- Low-confidence path → create `manual_review` record for DPO

**[S4-04] · 7pts**  
_As an ML engineer, I want the inference service to never log sample values so that the service itself does not become a PII leak._
- Structured logging: log only `column_id`, `table_id`, `pii_category`, `confidence`
- Unit test: assert no sample values appear in log output

**[S4-05] · 5pts**  
_As an operator, I want inference latency and throughput metrics so that I can scale the service under load._
- Prometheus metrics: request latency p50/p95/p99, requests/sec, model load time
- Load test: 100 concurrent column batches — assert p95 < 2s

---

## Sprint 5 — Remediation Engine
**Duration:** 2 weeks | **Points:** 40

### Goals
Automatic anonymization and quarantine of flagged data, with DPO notification.

### User Stories

**[S5-01] · 13pts**  
_As a data governance engineer, I want a PySpark anonymization job that masks PII columns in-place so that flagged tables comply with GDPR/LGPD without data loss._
- Anonymization strategies per PII type:
  - `EMAIL` → SHA-256 hash
  - `CREDIT_CARD` → last 4 digits only (mask first 12)
  - `SSN` / `CPF` → full redaction → `[REDACTED]`
  - `FULL_NAME` → format-preserving pseudonymization
  - `PHONE` → keep country code, mask rest
- Job: `etl/anonymizers/spark_anonymizer.py`
- Idempotent: columns already masked are skipped
- Writes audit record on completion

**[S5-02] · 10pts**  
_As a data governance engineer, I want a quarantine job that moves flagged data to an isolated S3 prefix so that sensitive data is immediately isolated pending DPO review._
- Move (not copy) raw data to `s3://pii-quarantine/{table_id}/`
- Apply bucket policy: write-only for pipeline, read restricted to `dpo` IAM role
- Update `pii_findings` status to `quarantined`
- Quarantine manifest written to `quarantine_manifest` table

**[S5-03] · 8pts**  
_As a DPO, I want to receive an automated notification when PII is detected and quarantined so that I can review and decide on next steps within the regulatory timeframe._
- Email: Jinja-templated report (table name, PII categories found, confidence, data owner, recommended action)
- Slack: webhook message with summary + link to dashboard
- Notification retried 3x on failure
- Delivery logged to `notifications` table

**[S5-04] · 5pts**  
_As a developer, I want the remediation DAG to be fully idempotent so that a pipeline failure and re-run does not corrupt already-remediated data._
- Test: run remediation DAG twice on same table — assert identical output
- Test: partial failure mid-job → re-run → assert clean completion

**[S5-05] · 4pts**  
_As an auditor, I want every remediation action recorded in an append-only audit log so that I can demonstrate regulatory compliance during an inspection._
- `audit_log` table: `id`, `event_type`, `table_id`, `actor`, `timestamp`, `details_json`
- Constraint: no UPDATE or DELETE allowed (enforced at DB level via trigger)

---

## Sprint 6 — Dashboard: Privacy Risk Inventory
**Duration:** 2 weeks | **Points:** 42

> ⚠️ All visual decisions in this sprint are governed by `BRAND.md`.  
> Every component must be reviewed against BRAND.md before implementation.

### Goals
A working React dashboard that gives DPOs and auditors full visibility into PII risk across the data infrastructure.

### User Stories

**[S6-01] · 5pts**  
_As a developer, I want the FastAPI backend to expose all dashboard endpoints so that the frontend has a stable API contract._
- `GET /api/v1/risks` — paginated, filterable by: PII category, status, data source, date range
- `GET /api/v1/stats/summary` — counts by status for KPI cards
- `GET /api/v1/tables/{id}/pii-report` — full column-level findings
- `POST /api/v1/tables/{id}/remediate` — manual remediation trigger (DPO role only)
- `GET /api/v1/audit-log` — paginated audit trail
- OpenAPI spec auto-generated and committed

**[S6-02] · 10pts**  
_As a DPO, I want a Risk Inventory overview page that shows all flagged tables ranked by severity so that I can prioritize my review queue._
- KPI cards: Total Flagged Tables | Tables Remediated | Pending Review | Compliance Score %
- Sortable, filterable data table: table name, data source, PII categories, confidence, status, last scanned
- Status badges styled per BRAND.md risk color mapping
- Pagination + URL-persisted filters
- Empty state + loading skeleton

**[S6-03] · 8pts**  
_As a DPO, I want a per-table PII Report page that shows column-level findings so that I understand exactly which columns contain which type of PII._
- Column breakdown table: column name, PII category, confidence score, sample count, status
- Confidence score rendered as a visual indicator (styled per BRAND.md)
- Action buttons: "Anonymize Now" | "Send to Quarantine" | "Mark as False Positive"
- Confirmation modal before triggering remediation

**[S6-04] · 8pts**  
_As an auditor, I want an Audit Log page that shows every action taken by the system so that I can produce evidence of compliance._
- Filterable by: actor, event type, date range
- Immutable presentation (no edit/delete UI)
- Export to CSV

**[S6-05] · 6pts**  
_As a DPO, I want a Data Sources map that visualizes where PII risk is concentrated across cloud regions and buckets so that I can see the full footprint of shadow data._
- Grouped by: AWS region, S3 bucket / Glue database
- Risk heatmap (color-coded by count and severity — BRAND.md palette)
- Click-through to filtered Risk Inventory

**[S6-06] · 5pts**  
_As any authenticated user, I want JWT-secured login with role-based access so that sensitive findings are protected._
- Login page (per BRAND.md auth screen spec)
- Roles: `admin`, `dpo`, `auditor`, `viewer`
- Route guards: `dpo`-only for remediation actions
- Token refresh + logout

---

## Sprint 7 — Hardening, Security Audit & Performance
**Duration:** 2 weeks | **Points:** 36

### Goals
Production-ready: security hardened, performance validated, all edge cases covered.

### User Stories

**[S7-01] · 10pts**  
_As a security engineer, I want a full security review of all components so that the system does not become a PII vector itself._
- Dependency audit: `pip audit`, `npm audit` — zero critical vulnerabilities
- Trivy scan on all Docker images — zero HIGH/CRITICAL CVEs
- OWASP Top 10 review on FastAPI (SQL injection, auth bypass, IDOR)
- Confirm: no PII values in any log stream (grep audit on log fixtures)
- Penetration test checklist documented in `docs/security-review.md`

**[S7-02] · 8pts**  
_As a platform engineer, I want load testing on the inference service and API so that I know the system handles peak load at scale._
- Locust load test: 500 concurrent users, 10-minute soak
- Targets: API p95 < 500ms | Inference p95 < 2s
- Identify and fix any bottleneck before production

**[S7-03] · 8pts**  
_As a developer, I want end-to-end tests covering the full ghost-data detection lifecycle so that the entire pipeline is validated as an integrated system._
- E2E test: upload a synthetic table with PII to S3 → assert it is detected, classified, remediated, and appears in dashboard within expected SLA
- Use a real (test-environment) Kafka + Airflow + inference service
- Run nightly in CI

**[S7-04] · 5pts**  
_As an operator, I want runbook documentation for all failure scenarios so that on-call engineers can resolve incidents quickly._
- Runbooks: patrol DAG failure | inference service down | quarantine bucket full | Kafka consumer lag spike
- Stored in `docs/runbooks/`

**[S7-05] · 5pts**  
_As a developer, I want all test coverage gaps from previous sprints closed so that the entire codebase meets the 80% threshold._
- Run coverage report across all modules
- Write missing unit tests to reach target
- Coverage badge in `README.md`

---

## Sprint 8 — Production Rollout & DPO Onboarding
**Duration:** 2 weeks | **Points:** 30

### Goals
System live in production. DPO and auditors trained and operational.

### User Stories

**[S8-01] · 8pts**  
_As a platform engineer, I want the full stack deployed to production AWS via Helm so that the system is live and monitored._
- Terraform: provision all AWS resources (VPC, EKS, RDS, ElastiCache, MSK, S3 buckets)
- Helm release for all services
- DNS + TLS configured for dashboard and API
- Production Grafana dashboards live
- PagerDuty alerts wired for critical failures

**[S8-02] · 8pts**  
_As a DevOps engineer, I want a blue/green deployment strategy for the inference service so that model updates cause zero downtime._
- Helm chart supports blue/green via weighted traffic split
- Rollback procedure documented and tested

**[S8-03] · 8pts**  
_As a DPO, I want onboarding documentation and a training session so that I can use the Privacy Risk Inventory effectively from day one._
- `docs/dpo-user-guide.md`: full walkthrough with screenshots
- `docs/dpo-quickstart.pdf`: 1-page cheat sheet (generated from BRAND.md template)
- Recorded walkthrough video (Loom)
- Onboarding checklist: accounts created, roles assigned, notification settings verified

**[S8-04] · 6pts**  
_As a platform engineer, I want a data retention policy enforced on quarantined data so that the quarantine zone does not grow indefinitely and remains compliant._
- S3 lifecycle rule: quarantined data reviewed within 30 days or auto-deleted
- Airflow DAG: `dag_quarantine_expiry.py` — notifies DPO 7 days before expiry
- Policy documented in `docs/data-retention-policy.md`

---

## Post-Launch Backlog (Sprint 9+)

These items are defined but not scheduled. Prioritize after M6 is stable.

| Item | Description | Estimated Points |
|---|---|---|
| PBI-01 | Support for Snowflake and BigQuery as data sources | 21 |
| PBI-02 | Self-learning: feed DPO false-positive markings back into model retraining | 34 |
| PBI-03 | LGPD-specific report template (Brazilian regulatory format) | 8 |
| PBI-04 | Slack bot: DPO can approve/reject quarantine from Slack thread | 13 |
| PBI-05 | Real-time scanning (sub-1h patrol) via Kafka Streams instead of batch DAG | 34 |
| PBI-06 | Multi-tenant support (isolate findings per business unit) | 21 |
| PBI-07 | GDPR Article 30 Records of Processing Activities (RoPA) auto-export | 13 |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ML model false positives cause valid data to be quarantined | Medium | High | 0.85 threshold + DPO manual review path for borderline cases |
| Inference service becomes a PII leak via logs | Low | Critical | Sprint 4 privacy logging test + Sprint 7 security audit |
| Sampling jobs cause performance degradation on source systems | Medium | High | Read-only IAM + time-window sampling (off-peak hours) |
| Kafka consumer lag causes detection delay > 24h SLA | Medium | Medium | Lag alerts in Sprint 1 + consumer group auto-scaling |
| Model accuracy degrades on new data formats | Low | High | Weekly retraining job (Sprint 3) + F1 regression alert |
| Regulatory scope expansion (new countries/laws) | High | Medium | Pluggable PII category system designed from Sprint 3 |
