# Shadow Data & PII Ghost-Hunter

[![CI](https://github.com/your-org/pii-ghost-hunter/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/pii-ghost-hunter/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-%E2%89%A580%25-brightgreen)](docs/coverage-report.md)
[![Security](https://img.shields.io/badge/security-OWASP%20reviewed-blue)](docs/security-review.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An automated data governance platform that detects and remediates **ghost data** — forgotten copies of sensitive databases (PII) scattered across Data Lakes, S3 buckets, and temporary warehouse tables — ensuring continuous compliance with GDPR and LGPD.

---

## What It Does

1. **Detects** — Kafka-based scanner watches for new table/file creation events across your data lake and warehouse.
2. **Classifies** — Fine-tuned DistilBERT model (multilingual: EN/ES/PT) classifies column samples into 10 PII categories with confidence scoring.
3. **Remediates** — PySpark anonymization jobs mask or quarantine flagged data automatically when confidence ≥ 0.85.
4. **Reports** — React dashboard gives DPOs a full Privacy Risk Inventory with per-table PII reports and an immutable audit trail.

---

## Quick Start

```bash
# Prerequisites: Docker, Docker Compose, Python 3.11+, Node 20+
cp .env.example .env          # fill in required vars
make dev                      # spins up all services
make test                     # runs unit + security tests
```

Open [http://localhost:5173](http://localhost:5173) and log in with `dpo@company.com` / `dpo`.

---

## Architecture

```
Kafka (table.created / file.moved)
  └─► Scanner Consumer ──► pii.candidates topic
        └─► Airflow Patrol DAG (daily)
              └─► Sampling Pipeline DAG
                    └─► PII Inference Service (DistilBERT)
                          └─► pii_findings (PostgreSQL)
                                └─► Remediation DAG (anonymize / quarantine)
                                      └─► audit_log ──► FastAPI ──► React Dashboard
```

See [CLAUDE.md](CLAUDE.md) for the full component reference, coding standards, and environment variable list.

---

## Test Suite

| Layer | Command | Coverage |
|---|---|---|
| Unit (API + scanner + ML + ETL) | `make test` | ≥ 80% |
| Security audit | `pytest tests/security/` | 16 tests |
| E2E ghost-data lifecycle | `pytest -m integration tests/e2e/` | Nightly CI |
| Load test (Locust) | `locust -f tests/perf/locustfile.py` | p95 < 500ms API / 2s inference |

---

## Security

- OWASP Top 10 review: [docs/security-review.md](docs/security-review.md)
- Hardened HTTP headers on every response (X-Content-Type-Options, CSP, HSTS behind proxy)
- Auth rate limiting: 10 attempts / 60s per IP → HTTP 429
- Trivy image scans in CI — zero HIGH/CRITICAL CVEs gate merges
- PII values are **never** logged — only table names, column names, and classification metadata

---

## Documentation

- [Getting Started](docs/getting-started.md)
- [Security Review](docs/security-review.md)
- [Data Retention Policy](docs/data-retention-policy.md)
- **DPO Onboarding**
  - [User Guide](docs/dpo-user-guide.md)
  - [Quick-Start](docs/dpo-quickstart.md)
  - [Onboarding Checklist](docs/dpo-onboarding-checklist.md)
- [Runbooks](docs/runbooks/)
  - [Patrol DAG Failure](docs/runbooks/patrol-dag-failure.md)
  - [Inference Service Down](docs/runbooks/inference-service-down.md)
  - [Inference Blue/Green Rollout](docs/runbooks/inference-bluegreen-rollout.md)
  - [Quarantine Bucket Full](docs/runbooks/quarantine-bucket-full.md)
  - [Kafka Consumer Lag Spike](docs/runbooks/kafka-consumer-lag-spike.md)
- [Brand & Design System](BRAND.md)
