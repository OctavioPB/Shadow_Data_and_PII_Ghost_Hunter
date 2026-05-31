# CLAUDE.md — Shadow Data & PII Ghost-Hunter

> This file is the source of truth for Claude Code when working on this project.
> Read it fully before writing any code, making architectural decisions, or touching infrastructure.

---

## Project Overview

**Shadow Data & PII Ghost-Hunter** is an automated data governance platform that detects and remediates "ghost data" — forgotten copies of sensitive databases (PII) scattered across Data Lakes, S3 buckets, and temporary warehouse tables — ensuring continuous compliance with GDPR and LGPD.

---

## Repository Structure

```
pii-ghost-hunter/
├── scanner/               # Kafka-based metadata stream consumer
│   ├── consumers/
│   ├── producers/
│   └── schemas/
├── orchestration/         # Airflow DAGs and plugins
│   ├── dags/
│   ├── plugins/
│   └── config/
├── ml/                    # PII classification model (Deep Learning)
│   ├── training/
│   ├── inference/
│   ├── models/
│   └── data/
├── etl/                   # Anonymization & quarantine pipelines
│   ├── anonymizers/
│   ├── quarantine/
│   └── notifiers/
├── api/                   # Backend API (FastAPI)
│   ├── routers/
│   ├── schemas/
│   └── services/
├── dashboard/             # React frontend — Privacy Risk Inventory
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   └── store/
│   └── public/
├── infra/                 # Terraform / Helm charts
│   ├── terraform/
│   └── helm/
├── tests/
├── docs/
├── CLAUDE.md              ← you are here
├── PLAN.md
└── BRAND.md               # UI/Design decisions — READ before touching the dashboard
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Stream Ingestion | Apache Kafka + Kafka Connect |
| Orchestration | Apache Airflow 2.x |
| ML / Classification | PyTorch + HuggingFace Transformers |
| ETL / Remediation | Apache Spark (PySpark) |
| Backend API | FastAPI + Pydantic v2 |
| Database (metadata) | PostgreSQL 15 |
| Cache | Redis 7 |
| Dashboard Frontend | React 18 + TypeScript + Vite |
| Infrastructure | Terraform + AWS (S3, Glue, Athena) |
| Containerization | Docker + Kubernetes (Helm) |
| CI/CD | GitHub Actions |
| Monitoring | Prometheus + Grafana |

---

## Core Components & Responsibilities

### 1. `scanner/` — Metadata Scanner
- Consumes Kafka topics: `table.created`, `file.moved`, `schema.changed`
- Parses Data Lake/Warehouse creation logs
- Publishes enriched events to `pii.candidates` topic
- **Never reads actual data rows** — only metadata at this stage

### 2. `orchestration/` — Airflow DAGs
- `dag_patrol_new_tables.py`: Runs every 24h, enqueues tables created in the last day
- `dag_sampling_pipeline.py`: Takes random column samples (max 1,000 rows) per suspicious table
- `dag_remediation.py`: Triggers anonymization or quarantine after ML verdict
- All DAG configs via environment variables — no hardcoded credentials

### 3. `ml/` — PII Classification Model
- Input: column samples (up to 1,000 values) + column metadata (name, dtype, table context)
- Output: classification label + confidence score per PII category
- PII categories: `SSN`, `CREDIT_CARD`, `EMAIL`, `PHONE`, `FULL_NAME`, `DATE_OF_BIRTH`, `ADDRESS`, `BANK_ACCOUNT`, `PASSPORT`, `NONE`
- Model: fine-tuned `distilbert-base-multilingual-cased` — must support Spanish, Portuguese, English
- Threshold for action: `confidence >= 0.85`
- Model artifacts stored in S3 `s3://pii-hunter-models/`

### 4. `etl/` — Remediation Engine
- `anonymizers/`: PySpark jobs for masking/tokenization per PII type
- `quarantine/`: Moves data to isolated S3 prefix `s3://pii-quarantine/`
- `notifiers/`: Sends alert to DPO via email + Slack webhook
- Remediation is **idempotent** — re-running must not corrupt already-clean data

### 5. `api/` — FastAPI Backend
- REST endpoints consumed by the dashboard
- Auth: JWT + role-based (roles: `admin`, `dpo`, `auditor`, `viewer`)
- Key endpoints:
  - `GET /api/v1/risks` — paginated risk inventory
  - `GET /api/v1/tables/{id}/pii-report` — detailed per-table report
  - `POST /api/v1/tables/{id}/remediate` — trigger manual remediation
  - `GET /api/v1/audit-log` — immutable audit trail

### 6. `dashboard/` — Privacy Risk Inventory UI
> ⚠️ **Before writing any frontend code, read `BRAND.md`.**
> All color tokens, typography, component patterns, icon sets, and tone-of-voice rules live there.
> Do not introduce new visual decisions without consulting BRAND.md first.

---

## Coding Standards

### Python
- Python 3.11+
- Follow PEP 8 strictly; use `ruff` as linter and `black` as formatter
- Type hints are **mandatory** on all function signatures
- Docstrings: Google style
- No bare `except:` clauses — always catch specific exceptions
- Secrets via environment variables only — never in code or committed config files
- Use `pydantic` for all data validation models

### TypeScript / React
- Strict TypeScript (`"strict": true` in tsconfig)
- Functional components only — no class components
- Custom hooks for all data-fetching logic (`useRiskInventory`, `usePIIReport`, etc.)
- State management: Zustand
- API calls: React Query (TanStack Query v5)
- No inline styles — use CSS modules or Tailwind utility classes per BRAND.md spec
- Accessibility: all interactive elements must have proper ARIA labels

### SQL
- All migrations via Alembic
- No raw string interpolation in queries — always use parameterized queries
- Index every foreign key and every column used in `WHERE` clauses

---

## Environment Variables

Never hardcode. All envs documented in `.env.example`. Required vars:

```
# Kafka
KAFKA_BOOTSTRAP_SERVERS=
KAFKA_SECURITY_PROTOCOL=

# Airflow
AIRFLOW__CORE__SQL_ALCHEMY_CONN=
AIRFLOW__CORE__FERNET_KEY=

# AWS
AWS_REGION=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
S3_DATA_LAKE_BUCKET=
S3_QUARANTINE_BUCKET=
S3_MODELS_BUCKET=

# Database
DATABASE_URL=
REDIS_URL=

# ML
MODEL_CONFIDENCE_THRESHOLD=0.85
MODEL_S3_PATH=

# Notifications
DPO_EMAIL=
SLACK_WEBHOOK_URL=

# Auth
JWT_SECRET_KEY=
JWT_ALGORITHM=HS256
```

---

## Privacy & Security Rules (Non-Negotiable)

1. **Never log PII values** — only log table names, column names, and classification results
2. **Sampling is destructive-read-only** — sampling jobs must not write to source tables
3. **Audit trail is append-only** — no UPDATE or DELETE on `audit_log` table
4. **All data in transit encrypted** — TLS 1.2+ for Kafka, HTTPS for APIs
5. **Quarantine bucket is write-only for pipeline** — only DPO role can read from it
6. **Model inference input must be anonymized in logs** — store only column metadata, not sample values

---

## Testing Requirements

- Unit tests: `pytest` — minimum 80% coverage on `ml/`, `etl/`, `api/`
- Integration tests: use `testcontainers` for Kafka and PostgreSQL
- ML model tests: include a fixture of known PII patterns (SSN, VISA card numbers, emails) and confirm classification correctness
- Frontend: Vitest + React Testing Library — test all page-level components
- Run `make test` before every PR

---

## CI/CD Pipeline (GitHub Actions)

On every PR:
1. Lint (ruff, black --check, tsc)
2. Unit tests
3. Docker build (all services)
4. Security scan (Trivy on images)

On merge to `main`:
1. All PR checks +
2. Integration tests
3. Push images to ECR
4. Deploy to staging via Helm

---

## Key Constraints for Claude Code

- When modifying DAGs, always check idempotency — DAGs may re-run on failure
- When touching the ML model pipeline, never change the output schema without updating the API serializer
- When adding a new PII category, update: model training labels + API schema + dashboard filter list + BRAND.md risk color mapping
- Dashboard changes require a visual QA note in the PR description referencing the BRAND.md section consulted
- Infrastructure changes (Terraform) require a `terraform plan` output attached to the PR

---

## Glossary

| Term | Definition |
|---|---|
| Shadow Data | PII-containing copies of production data forgotten in non-production environments |
| Ghost Table | A temporary table or S3 prefix no longer referenced by any active pipeline |
| DPO | Data Protection Officer — the human responsible for privacy compliance |
| PII | Personally Identifiable Information |
| Quarantine Zone | Isolated, access-restricted S3 prefix for flagged data pending DPO review |
| Confidence Score | ML model output (0–1) indicating certainty that a column contains PII |
| Patrol DAG | The Airflow DAG that scans newly created tables every 24 hours |
