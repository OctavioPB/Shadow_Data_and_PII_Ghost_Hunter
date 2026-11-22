# Shadow Data & PII Ghost-Hunter

[![CI](https://github.com/OctavioPB/Shadow_Data_and_PII_Ghost_Hunter/actions/workflows/ci.yml/badge.svg)](https://github.com/OctavioPB/Shadow_Data_and_PII_Ghost_Hunter/actions/workflows/ci.yml)
[![Security](https://img.shields.io/badge/security-OWASP%20reviewed-blue)](docs/security-review.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An automated data-governance platform that detects and remediates **ghost data**: forgotten copies of sensitive (PII) datasets scattered across data lakes, S3 buckets and temporary warehouse tables. It is built for continuous GDPR / LGPD compliance.

---

## What it does

1. **Detects.** A Kafka scanner consumes `table.created` and `file.moved` (S3 EventBridge) events and records every new table or file.
2. **Samples and classifies.** Airflow samples up to 1,000 rows per source. A fine-tuned multilingual DistilBERT model (EN/ES/PT) then classifies each column into 9 PII categories plus `NONE`. A column is flagged when confidence ≥ 0.85.
3. **Remediates.** For S3 objects of a known format (parquet, csv, json), the remediation DAG runs these steps in order:
   1. anonymizes the flagged columns into a staging prefix;
   2. moves the raw data to an isolated quarantine bucket;
   3. promotes the anonymized copy back to the original location;
   4. notifies the DPO (email / Slack).

   Glue/Athena catalog tables are reported to the DPO but not moved automatically.
4. **Retains.** Quarantined data is deleted after 30 days, and owners are warned 7 days before deletion.
5. **Reports.** A React dashboard gives DPOs:
   - a Privacy Risk Inventory with per-table PII reports;
   - lineage of copies;
   - ROPA (GDPR Art. 30), DSAR search (Art. 15) and compliance trends;
   - an append-only audit trail.

---

## Quick start

Prerequisites: Docker + Docker Compose, Python 3.11, Node 20.

```bash
cp .env.example .env     # set JWT_SECRET_KEY at minimum
make install             # Python + Node dependencies (for local tooling and tests)
make dev                 # builds and starts every service
make migrate             # creates the schema in the pii_hunter database (required once)
```

Open <http://localhost:5173> and log in with one of the development users:

| User | Password | Role |
|---|---|---|
| `dpo@company.com` | `dpo` | dpo (can remediate, annotate ROPA, run DSAR searches) |
| `admin@company.com` | `admin` | admin |
| `auditor@company.com` | `auditor` | auditor (read-only) |
| `viewer@company.com` | `viewer` | viewer (read-only) |

Passwords can be overridden with `DPO_PASSWORD`, `ADMIN_PASSWORD`, etc. This is a static development user store; replace it before any real deployment.

### Demo mode (Windows)

`demo.ps1` starts the infrastructure, runs migrations and launches the API and dashboard locally in one step. To get sample data, set `DEMO_MODE=true` for the API. The login page then offers a **demo seed**: 12 synthetic tables across 5 source types, with findings, quarantine entries and audit history. No real PII is stored.

```powershell
./demo.ps1                 # add -WithInference to also build the ML service (~3 GB first time)
```

### Services

| Service | URL |
|---|---|
| Dashboard | <http://localhost:5173> |
| API (+ OpenAPI docs at `/docs`) | <http://localhost:8000> |
| PII inference service | <http://localhost:8001> |
| Airflow | <http://localhost:8080> (admin / admin) |
| Kafka / Schema Registry | `localhost:9092` / <http://localhost:8081> |
| PostgreSQL / Redis | `localhost:5432` / `localhost:6379` |

---

## Architecture

```
Kafka (table.created / file.moved)
  └─► Scanner consumers ──► scanner_events (PostgreSQL) ──► pii.candidates (Avro)
        │                     malformed events ──► <topic>.dlq
        └─► Airflow patrol_new_tables (daily)
              └─► sampling_pipeline  (per table)
                    sample ──► column_samples
                    └─► PII inference service (DistilBERT) ──► pii_findings
                          ├─ flagged ──► remediation
                          │                anonymize (Spark → staging prefix)
                          │                → quarantine raw data (quarantine bucket)
                          │                → promote anonymized copy
                          │                → notify DPO → audit_log
                          └─ clean   ──► manual review record
        quarantine_expiry (daily) ──► 7-day warning, 30-day deletion

audit_log / pii_findings ──► FastAPI ──► React dashboard
```

| Path | Component |
|---|---|
| `scanner/` | Kafka consumers, Avro producer, SQLAlchemy models |
| `orchestration/dags/` | Airflow DAGs: patrol, sampling, remediation, quarantine expiry |
| `orchestration/plugins/` | S3 / Athena samplers, PII classifier operator |
| `ml/` | Synthetic data generator, training/evaluation, inference service |
| `etl/` | Spark anonymizer, quarantine job, DPO notifier |
| `api/` | FastAPI service (risks, audit, compliance, DSAR, auth) |
| `dashboard/` | React + TanStack Query dashboard |
| `alembic/` | Database migrations |
| `infra/` | Helm charts, Terraform, Grafana dashboards, alerting |

### Remediation guarantees

- **Sequential, never parallel.** Anonymization and quarantine both touch the source path.
- **Raw data is always contained.** Quarantine runs even if anonymization fails.
- **No partial output is promoted.** The anonymized copy replaces the source only after both previous steps succeed.
- **Failures are reported.** The DPO is always notified, and the audit record says `remediation_failed` when a step failed.
- **Every step is idempotent.** Retries upsert findings and skip work that is already done.

---

## Configuration

All settings are environment variables (see `.env.example`). The most relevant ones:

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET_KEY` | insecure dev key (logs a warning) | Signs access tokens; **must** be set outside development |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token lifetime |
| `DATABASE_URL` | local `pii_hunter` | PostgreSQL connection |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173` | Comma-separated dashboard origins |
| `API_DOCS_ENABLED` | `true` | Set `false` in production to hide `/docs` and `/openapi.json` |
| `TRUST_PROXY_HEADERS` | `false` | Honor `X-Forwarded-For` for rate limiting (only behind a proxy that overwrites it) |
| `DEMO_MODE` | `false` | Enables `POST /api/v1/demo/seed` |
| `DSAR_HASH_KEY` | `JWT_SECRET_KEY` | HMAC key for DSAR identifier hashes |
| `MODEL_S3_PATH` / `MODEL_DEVICE` | — / `-1` | Model location; `-1` = CPU, `N` = GPU index |
| `MODEL_CONFIDENCE_THRESHOLD` | `0.85` | Flagging threshold |
| `DPO_EMAIL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SLACK_WEBHOOK_URL` | — | DPO notifications (STARTTLS used when offered) |
| `S3_STAGING_BUCKET`, `S3_QUARANTINE_BUCKET` | `pii-hunter-staging`, `pii-quarantine` | Sampling output and quarantine |
| `QUARANTINE_RETENTION_DAYS`, `QUARANTINE_WARNING_DAYS_BEFORE` | `30`, `7` | Retention policy |

---

## Development

```bash
make test      # Python (pytest + coverage) and dashboard (Vitest)
make lint      # ruff + black, eslint + tsc + prettier
make format    # auto-format Python and TypeScript
make migrate-new MSG="describe change"   # new Alembic migration
```

| Suite | Command | Notes |
|---|---|---|
| Unit: API, scanner, ML, ETL | `pytest` | Airflow tests skip automatically outside the Airflow image |
| Airflow DAGs and operators | see below | Airflow 2.8 needs SQLAlchemy < 2, so it cannot share the API's environment |
| Security | `pytest tests/security/` | Headers, auth, rate limiting, no PII in logs or exports |
| Integration / E2E | `pytest -m integration tests/integration tests/e2e` | Requires Docker (testcontainers) |
| Load | `locust -f tests/perf/locustfile.py` | Targets: p95 < 500 ms API, < 2 s inference |
| Dashboard | `npm --prefix dashboard test` | Vitest |

Airflow tests, as run in CI:

```bash
docker run --rm -v "$PWD:/opt/app" -w /opt/app \
  -e PYTHONPATH=/opt/app/orchestration/plugins \
  --entrypoint bash apache/airflow:2.8.1-python3.11 -c \
  "pip install -q pytest==8.2.0 pytest-mock==3.14.0 pytest-asyncio==0.23.6 && \
   python -m pytest -o addopts='' --noconftest tests/orchestration"
```

Dependency layout:
- `requirements.txt`: full Python stack.
- `api/requirements.txt`: what the API image installs.
- `requirements-dev.txt`: test and lint tools.

The Airflow providers are not pip-installed; they come with the `apache/airflow` image.

---

## Security

- OWASP Top 10 review: [docs/security-review.md](docs/security-review.md).
- Security headers on every response: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store`, plus HSTS behind an HTTPS proxy.
- Authentication:
  - JWT with role-based access (dpo / admin / auditor / viewer);
  - constant-time password comparison;
  - login rate limit of 10 attempts per 60 s per client → HTTP 429.
- Injection hardening:
  - parameterized SQL everywhere;
  - validated Athena identifiers;
  - JSON audit details built with `json.dumps`;
  - CSV exports neutralize spreadsheet formulas.
- PII values are **never** logged or stored. Only table names, column names and classification metadata are kept. DSAR identifiers are stored as keyed HMACs.
- `audit_log` is append-only, enforced by a database trigger.
- Containers run as non-root. CI scans images with Trivy.

---

## Known limitations

- The `apache/airflow` image used by docker-compose has no pyspark or JVM. Locally, anonymization therefore fails and the remediation DAG falls back to quarantine plus DPO notification.
- Automatic remediation covers S3 objects only. Catalog tables (Glue/Athena) are flagged and reported but not moved.
- `pip-audit` reports known vulnerabilities in the pinned ML stack (torch, transformers, mlflow) and in parts of the web stack. Upgrades are pending.
- The user store is static (development only).

---

## Documentation

- [Getting Started](docs/getting-started.md)
- [Security Review](docs/security-review.md)
- [Data Retention Policy](docs/data-retention-policy.md)
- **DPO onboarding**
  - [User Guide](docs/dpo-user-guide.md)
  - [Quick-Start](docs/dpo-quickstart.md)
  - [Onboarding Checklist](docs/dpo-onboarding-checklist.md)
- [Runbooks](docs/runbooks/)
  - [Patrol DAG Failure](docs/runbooks/patrol-dag-failure.md)
  - [Inference Service Down](docs/runbooks/inference-service-down.md)
  - [Inference Blue/Green Rollout](docs/runbooks/inference-bluegreen-rollout.md)
  - [Quarantine Bucket Full](docs/runbooks/quarantine-bucket-full.md)
  - [Kafka Consumer Lag Spike](docs/runbooks/kafka-consumer-lag-spike.md)
- Planning: [Roadmap](planning/ROADMAP.md) · [Decisions](planning/DECISIONS.md) · [Brand & Design System](planning/BRAND.md)

---

## License

Released under the [MIT License](LICENSE).
