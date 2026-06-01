# Engineering Decisions — Shadow Data & PII Ghost-Hunter

> This document records **every major engineering decision** made in this project,
> the alternatives that were considered, and the specific reason each decision was made.
> It is written so an engineering student can present the system and answer "why did you
> choose X instead of Y?" for any component.

---

## Table of Contents

1. [Problem Framing](#1-problem-framing)
2. [System Architecture — Overall Pattern](#2-system-architecture--overall-pattern)
3. [Stream Ingestion — Apache Kafka](#3-stream-ingestion--apache-kafka)
4. [Workflow Orchestration — Apache Airflow](#4-workflow-orchestration--apache-airflow)
5. [ML Model — Fine-tuned DistilBERT Multilingual](#5-ml-model--fine-tuned-distilbert-multilingual)
6. [Confidence Threshold — 0.85](#6-confidence-threshold--085)
7. [ML Input — Column Name + Values Concatenation](#7-ml-input--column-name--values-concatenation)
8. [ETL / Remediation Engine — PySpark](#8-etl--remediation-engine--pyspark)
9. [Anonymization Strategies — Per-Category Decisions](#9-anonymization-strategies--per-category-decisions)
10. [Backend API — FastAPI + Pydantic v2](#10-backend-api--fastapi--pydantic-v2)
11. [Async Database Layer — SQLAlchemy 2 + asyncpg](#11-async-database-layer--sqlalchemy-2--asyncpg)
12. [SQL Query Style — Raw text() with Named Parameters](#12-sql-query-style--raw-text-with-named-parameters)
13. [Database — PostgreSQL 15](#13-database--postgresql-15)
14. [Cache — Redis 7](#14-cache--redis-7)
15. [Authentication — JWT with HS256 + RBAC](#15-authentication--jwt-with-hs256--rbac)
16. [Rate Limiting — In-Memory Sliding Window](#16-rate-limiting--in-memory-sliding-window)
17. [Security Headers — Starlette Middleware](#17-security-headers--starlette-middleware)
18. [CORS Policy — Restricted Methods and Origins](#18-cors-policy--restricted-methods-and-origins)
19. [Privacy Logging — structlog, Metadata-Only](#19-privacy-logging--structlog-metadata-only)
20. [Frontend Framework — React 18 + TypeScript + Vite](#20-frontend-framework--react-18--typescript--vite)
21. [State Management — Zustand](#21-state-management--zustand)
22. [Data Fetching — TanStack Query v5](#22-data-fetching--tanstack-query-v5)
23. [Token Storage — sessionStorage over localStorage](#23-token-storage--sessionstorage-over-localstorage)
24. [Quarantine Zone — S3 Prefix Isolation](#24-quarantine-zone--s3-prefix-isolation)
25. [Data Retention — 30-Day Quarantine Window](#25-data-retention--30-day-quarantine-window)
26. [Append-Only Audit Log](#26-append-only-audit-log)
27. [Idempotency in All DAGs](#27-idempotency-in-all-dags)
28. [Sampling — Maximum 1,000 Rows](#28-sampling--maximum-1000-rows)
29. [Infrastructure — AWS over Other Clouds](#29-infrastructure--aws-over-other-clouds)
30. [Compute — EKS over ECS or Lambda](#30-compute--eks-over-ecs-or-lambda)
31. [Managed Kafka — MSK over Self-Managed](#31-managed-kafka--msk-over-self-managed)
32. [Database HA — RDS Multi-AZ for Production](#32-database-ha--rds-multi-az-for-production)
33. [Infrastructure-as-Code — Terraform](#33-infrastructure-as-code--terraform)
34. [Deployment Strategy — Blue/Green for Inference](#34-deployment-strategy--bluegreen-for-inference)
35. [Helm Umbrella Chart over Kustomize](#35-helm-umbrella-chart-over-kustomize)
36. [CI/CD — GitHub Actions](#36-cicd--github-actions)
37. [Monitoring — Prometheus + Grafana](#37-monitoring--prometheus--grafana)
38. [Testing Strategy — Unit + Security + E2E + Load](#38-testing-strategy--unit--security--e2e--load)
39. [Integration Testing — Testcontainers](#39-integration-testing--testcontainers)
40. [Load Testing — Locust](#40-load-testing--locust)
41. [Migrations — Alembic](#41-migrations--alembic)
42. [Repository Layout — Monorepo](#42-repository-layout--monorepo)
43. [Python Standards — ruff + black + type hints](#43-python-standards--ruff--black--type-hints)

---

## 1. Problem Framing

### Decision
Build an **automated, event-driven detection pipeline** for shadow data (forgotten PII-containing copies of production tables), rather than relying on manual data governance audits or rule-based scanning alone.

### Why
Large organizations continuously create temporary copies of data (analytics migrations, ETL staging tables, developer test dumps, S3 uploads from legacy scripts). These copies are often forgotten — they accumulate in data lakes and warehouse environments without any governance record. Manual audits happen quarterly at best; new tables appear daily.

The core insight is: **the cost of detecting a ghost table is low (automated scan), but the regulatory cost of missing one (GDPR Article 83 fine: up to 4% of global turnover) is catastrophic**. Automating detection at the event level (when a table is created) means no table ever escapes the inspection queue.

### Alternatives Considered
- **Manual quarterly audits:** Too slow. A table created on day 1 of the quarter could expose PII for 90 days before detection.
- **Rule-based regex scanning only:** Regular expressions can detect obvious patterns (e.g., `\d{3}-\d{2}-\d{4}` for SSN) but fail on obfuscated column names (`field_007`, `col_x`) and fail entirely for non-English PII formats (Brazilian CPF, Spanish DNI). Zero false-positive handling.
- **Buy a COTS DLP tool:** Commercial Data Loss Prevention tools like Macie or Informatica are expensive, often lack Latin American PII support, and cannot be extended with custom detection logic.

---

## 2. System Architecture — Overall Pattern

### Decision
**Event-driven microservices pipeline**: Scanner → Kafka → Airflow → Inference → Remediation → API → Dashboard.

```
New table created
      │
      ▼
Kafka (table.created)
      │
      ▼
Scanner Consumer ──► pii.candidates topic
      │
      ▼
Airflow Patrol DAG (daily)
      │
      ▼
Sampling DAG ──► S3 staging bucket
      │
      ▼
PII Inference Service (DistilBERT)
      │
      ▼
pii_findings (PostgreSQL)
      │
      ▼
Remediation DAG (anonymize / quarantine)
      │
      ▼
audit_log ──► FastAPI ──► React Dashboard
```

### Why
Each stage of detection is a distinct, independently scalable concern:
- **Scanner** runs continuously with low CPU (metadata-only, no data reading).
- **Airflow** provides retry, backfill, and scheduling for the slow batch phases (sampling + inference).
- **Inference** is compute-intensive and must scale independently of the rest of the pipeline.
- **API + Dashboard** are user-facing and must remain available even if the pipeline is paused for maintenance.

Coupling all of this into a monolith would mean that a slow inference job blocks the API, or a Kafka consumer bug takes down the dashboard.

### Alternative Considered
- **Synchronous monolith:** Scan → classify → remediate all in one process. Simple for small deployments, but completely unscalable: inference takes 2–15 seconds per table; with 500 new tables/day this creates a 17-minute blocking queue.
- **Serverless (Lambda):** Each stage as a Lambda function. Viable for low-volume use cases, but Lambda has a 15-minute execution limit, cold start penalties for ML model loading (~30 seconds for DistilBERT), and no built-in support for long-running DAG dependencies.

---

## 3. Stream Ingestion — Apache Kafka

### Decision
Use **Apache Kafka** as the event bus for table creation and file movement events. Topics: `table.created`, `file.moved`, `schema.changed`, `pii.candidates`.

### Why
Data warehouse systems (AWS Glue, Redshift, Athena, S3 Event Notifications) emit creation events as streams. Kafka is the industry-standard solution for this exact problem:

1. **Durability:** Events are persisted to disk with configurable retention (7 days by default). If the scanner consumer is down for a weekend, it processes the backlog on restart without losing any events. No other message queue provides this at the same scale.
2. **Replayability:** Kafka allows consumers to replay from any offset. This is critical for this project: if we deploy a new, more accurate version of the PII classifier, we can replay the last 30 days of `pii.candidates` events through the new model without re-scanning source tables.
3. **Multiple consumers:** `pii.candidates` is consumed by both the Airflow operator (for classification) and by a future audit consumer. With Kafka, adding a new consumer group costs nothing — the original consumer is unaffected.
4. **Ordering within partition:** Events for the same table are hashed to the same partition, ensuring the `table.created` event is always processed before `schema.changed` for the same table.

### Alternatives Considered
- **AWS SQS/SNS:** Managed and simpler. However, SQS has a 14-day message retention limit, no replay capability (deleted on consumption), and FIFO queues cap at 300 TPS — insufficient for high-volume data lakes. Most importantly, SQS does not allow multiple consumer groups to read the same message independently.
- **AWS Kinesis:** Similar to Kafka semantically. However, it shards are limited to 1MB/s and 1,000 records/s per shard, and shard management is manual. MSK (Managed Kafka on AWS) was chosen over Kinesis for Kafka's richer ecosystem and the team's existing Kafka expertise.
- **RabbitMQ:** Message queuing, not log streaming. No replay capability. Once a message is consumed, it is gone. Not appropriate for an audit-grade pipeline where replaying events is a required feature.
- **Redis Streams:** Lightweight but lacks multi-consumer-group semantics at scale. Fine for small systems, but the operational model diverges from Kafka significantly as the team scales.

---

## 4. Workflow Orchestration — Apache Airflow

### Decision
Use **Apache Airflow 2.x** with `@dag` and `@task` decorators (TaskFlow API) for the patrol, sampling, remediation, and quarantine-expiry DAGs.

### Why
The detection pipeline is fundamentally a **batch workflow with complex dependency graphs**:
- Sampling cannot start before patrol marks a table as `queued`.
- Inference cannot start before sampling writes column samples to S3.
- Remediation cannot start before inference returns a result above the confidence threshold.
- If any step fails, only that step should retry — not the entire pipeline from scratch.

Airflow was built exactly for this pattern. Specific reasons:
1. **Retry with backoff:** Every task has `retries=2` and `retry_delay=timedelta(minutes=5)`. A transient S3 timeout at the sampling step does not restart the entire DAG.
2. **Idempotency by design:** DAGs are designed to re-run safely (`@daily` schedule, idempotent upserts in PostgreSQL). This is enforced by the CLAUDE.md constraint: "always check idempotency — DAGs may re-run on failure."
3. **Backfill:** If the patrol DAG misses a day (infrastructure downtime), Airflow can backfill the missed runs automatically.
4. **Observability:** The Airflow UI shows per-task success/failure rates, logs, and run history — critical for a DPO who needs to verify that every new table was inspected.
5. **PostgreSQL-native:** Airflow uses PostgreSQL for its own metadata store, the same database used for the application schema — consistent operational model.

### Alternatives Considered
- **Prefect:** Modern, Python-native orchestrator. Strong developer experience. However, the self-hosted server setup is less mature than Airflow for large enterprise deployments. The team selected Airflow for its larger community, extensive AWS connector library, and wider DPO familiarity.
- **AWS Step Functions:** Managed, serverless orchestration. Excellent for simple linear workflows. However, building complex DAG dependencies in Step Functions requires JSON state machine definitions that are hard to version-control and test. No concept of "backfill." Debugging a failed step requires navigating the AWS console rather than reading Python code.
- **Dagster:** Strong asset-based model (defines data assets rather than tasks). Conceptually attractive but introduces a paradigm shift that would require retraining the team. Airflow's `@task` decorator API (TaskFlow) provides sufficient structure.
- **Plain cron:** Cannot handle retries, inter-task dependencies, or backfill. Completely opaque to observability.

---

## 5. ML Model — Fine-tuned DistilBERT Multilingual

### Decision
Fine-tune **`distilbert-base-multilingual-cased`** from HuggingFace Transformers on a synthetic labeled dataset of column samples for 10 PII categories.

### Why Each Component of This Decision Matters

**Why a deep learning model instead of regex-only?**

Regular expressions are brittle for PII detection in real data lakes because:
- Column names in real warehouses are often obfuscated: `field_07`, `attr_x`, `tmp_val_22`. A regex applied to values can detect `123-45-6789` as an SSN, but only if you know to look in that column. The DL model learns that *the combination of column name context and value patterns* predicts PII category.
- Brazilian CPF (`123.456.789-09`) and CURP formats differ from US equivalents. Maintaining separate regex sets for 10+ PII types across 3 languages is an unbounded maintenance burden. The model generalizes from training data.
- Regex cannot handle partially obfuscated data: `***-45-6789` (partially masked SSN already in the source) or `al***@example.com`.

**Why DistilBERT specifically?**

- `distilbert-base-multilingual-cased` is a knowledge-distilled version of BERT, 40% smaller (66M vs. 110M parameters) and 60% faster at inference, while retaining 97% of BERT's performance on sequence classification benchmarks.
- Inference latency is a hard SLA: p95 < 2 seconds per batch of columns. Full BERT exceeds this constraint on CPU; DistilBERT fits comfortably.
- The `multilingual-cased` variant covers 104 languages in a single model checkpoint, including English, Spanish, and Portuguese — the three languages required by the GDPR (EU) and LGPD (Brazil) scope of this project.

**Why not GPT-4 or a large language model?**

- LLM APIs introduce a privacy violation: sending column samples containing actual PII to an external API endpoint is itself a GDPR breach. The classification must run on self-hosted infrastructure where data never leaves the organization's compute boundary.
- GPT-4 inference latency (network + model) would push p95 well above 2 seconds for batch classification.
- Fine-tuned DistilBERT achieves target F1 ≥ 0.90 per class — sufficient for the task. LLM cost and latency overhead is not justified by marginal accuracy gain.

**Why not spaCy NER?**

- spaCy's NER recognizes named entity types (PERSON, ORG, GPE) that do not map cleanly to PII categories (SSN, CREDIT_CARD, BANK_ACCOUNT, PASSPORT). Custom NER training for these domain-specific categories requires more labeled data and longer training cycles than fine-tuning an existing BERT variant.
- DistilBERT's text classification pipeline is simpler operationally: one model artifact, one predict call, one output per input text.

**Why not Amazon Comprehend?**

Same reasoning as GPT-4: external API = data leaving the perimeter. Comprehend also does not natively support LGPD-specific PII categories (CPF, CNPJ, Brazilian passport formats).

---

## 6. Confidence Threshold — 0.85

### Decision
Flag a column for mandatory review when **model confidence ≥ 0.85**. Automatically trigger remediation when confidence ≥ 0.85 (no human-in-the-loop for high-confidence findings).

### Why
This is a precision-recall trade-off with asymmetric costs:
- **False negative (miss real PII):** Regulatory fine, data breach, reputational damage. Very high cost.
- **False positive (flag clean data):** DPO reviews a column that turns out not to be PII. Low cost — inconvenient but not damaging.

Given this asymmetry, 0.85 is chosen as the threshold that maximizes recall (catching PII) at an acceptable false-positive rate. The evaluation report (Sprint 3) confirmed F1 ≥ 0.90 per class at this threshold.

- **< 0.70:** Too many false positives. Every column gets flagged, DPO becomes desensitized ("alert fatigue"), genuine PII findings are ignored.
- **0.70–0.84:** Manual DPO review path is triggered. DPO sees the finding but system does not auto-remediate.
- **≥ 0.85:** Automatic remediation. Model is confident enough to act without human confirmation.
- **≥ 0.95:** Only used in the "auto-quarantine without DPO notification" path if such a policy is added later.

The threshold is not hardcoded: it is set via `MODEL_CONFIDENCE_THRESHOLD` environment variable, allowing DPOs to tune it per deployment without a code change.

---

## 7. ML Input — Column Name + Values Concatenation

### Decision
Format inference input as: `"{column_name}: {value}"` for each sample value, then classify up to 10 samples per column and return the maximum-confidence prediction.

```python
texts = [f"{column_name}: {v}" for v in sample_values]
```

### Why
BERT-family models are trained on natural language sequences. Prepending the column name to each value gives the model critical context:
- `"email_address: alice@example.com"` → strong signal for EMAIL
- `"field_007: alice@example.com"` → model learns from the value alone (column name provides no context, but the value pattern is still classifiable)
- `"created_at: alice@example.com"` → model can resolve the contradiction (a timestamp column containing email-like values signals a data integrity problem or a misidentified PII column)

This concatenation approach was validated in the Sprint 3 evaluation: F1 improved by ~6 percentage points on the obfuscated-column-name test set compared to value-only classification.

Taking the **maximum-confidence prediction across samples** (not average) is deliberate: if even one row in a 1,000-row sample is clearly an SSN, the column is flagged. PII often appears in only a subset of rows (nullable columns, batch-appended data).

---

## 8. ETL / Remediation Engine — PySpark

### Decision
Use **PySpark** for the anonymization and quarantine ETL jobs (`etl/anonymizers/spark_anonymizer.py`, `etl/quarantine/quarantine_job.py`).

### Why
PII-containing tables in a production data lake can be arbitrarily large — hundreds of millions of rows. The anonymization job must:
1. Read source data from S3 (Parquet, CSV, or JSON).
2. Apply per-column transformation functions.
3. Write the transformed data back to S3.
4. Do this without loading the entire dataset into memory on a single machine.

PySpark is the established framework for this class of problem: distributed data processing with a rich ecosystem of connectors for AWS S3, Glue, Redshift, and Athena.

Specific design choices within PySpark:
- **UDF-compatible strategy functions:** Each anonymization strategy (`anonymize_email`, `anonymize_credit_card`) is a pure Python function with no external dependencies, making them directly usable as Spark UDFs. This was deliberately designed in `etl/anonymizers/strategies.py`.
- **Idempotency via sentinel detection:** Each strategy function checks whether its input is already in the output format (e.g., `_HASH_RE.match(value)` for SHA-256 hashes). Re-running the anonymizer on an already-anonymized table is a no-op, satisfying the CLAUDE.md idempotency requirement.
- **Read-only on source tables:** The sampling jobs access source tables with read-only IAM credentials. Anonymized output is written to a new S3 path. This satisfies the CLAUDE.md security rule: "Sampling is destructive-read-only — sampling jobs must not write to source tables."

### Alternatives Considered
- **pandas + Python:** Would run on a single machine. Adequate for tables up to ~100MB; completely unusable for a 10TB data lake table. Pandas `apply()` on 500M rows would run for hours.
- **AWS Glue:** Managed PySpark execution environment. Viable, but introduces vendor lock-in for the ETL layer. Glue jobs are defined in the AWS console or JSON configs, harder to unit-test than local PySpark jobs. The team preferred testable, local-runnable Python code.
- **dbt:** SQL-based transformation tool. Excellent for analytics transformations, but dbt operates on data in the warehouse, not data at rest in S3. Cannot handle the Parquet/CSV raw format in the data lake.

---

## 9. Anonymization Strategies — Per-Category Decisions

Each PII category uses a different transformation because the analytical use case and format requirements differ:

| Category | Strategy | Justification |
|---|---|---|
| **EMAIL** | SHA-256 hex hash | Preserves uniqueness (same email → same hash) so join keys survive anonymization. One-way function: original email cannot be recovered. |
| **CREDIT_CARD** | Keep last 4 digits: `****-****-****-1234` | Industry standard (PCI-DSS). Last 4 digits are used for customer verification; full number is the sensitive component. Format preservation allows display in UI without re-engineering downstream systems. |
| **SSN / CPF** | Full redaction → `[REDACTED]` | No analytical value to any part of a social security number. Keeping any digits would create partial PII (e.g., last 4 digits of SSN are still sensitive). |
| **FULL_NAME** | Format-preserving pseudonymization | Names must retain linguistic plausibility for downstream NLP models that process free-text fields. Random string replacement breaks word embeddings trained on real names. |
| **PHONE** | Keep country code, mask rest: `+1-**********` | International prefix is needed for geolocation analytics. The subscriber number is the PII component. |
| **DATE_OF_BIRTH** | Generalize to year only: `1985` | Year of birth retains demographic utility for age cohort analysis. Exact date is sensitive (used in identity verification). |
| **ADDRESS** | Redact street-level, keep city/state | City/state is used for geographic analytics. Street address is personally identifiable. |
| **BANK_ACCOUNT** | Full redaction | Account numbers have no analytical use. Any partial preservation (routing number) still constitutes sensitive financial data. |
| **PASSPORT** | Full redaction | Government-issued document numbers are highly sensitive and have no analytical utility in a data warehouse context. |

All strategies are **idempotent**: applying the function twice to the same input produces the same output. This is enforced by checking sentinel patterns (`_HASH_RE`, `_MASKED_CARD_RE`, `_REDACTED`) at the start of each function.

---

## 10. Backend API — FastAPI + Pydantic v2

### Decision
Build the REST API using **FastAPI 0.111.0** with **Pydantic v2** for request/response validation.

### Why
FastAPI was selected over alternatives for a specific combination of properties:

1. **Async-native:** All database calls use `await`, enabling the API server to handle many concurrent requests on a single process without blocking. This is critical for the dashboard: during peak DPO usage, multiple users load the risk inventory simultaneously. FastAPI on `uvicorn` (ASGI) handles this natively; Flask (WSGI) would require a thread-per-request model with much higher memory overhead.

2. **Automatic OpenAPI spec:** FastAPI auto-generates `/docs` and `/openapi.json` from the Pydantic schemas. The frontend team can use the spec to validate API contracts without manual documentation. The PLAN.md explicitly requires "OpenAPI spec auto-generated and committed."

3. **Pydantic v2 validation:** Every API request body and response is validated by Pydantic models. A POST to `/remediate` with `action: "' OR 1=1 --"` returns a 422 Validation Error before the code ever touches the database. This is a second layer of input sanitization on top of SQL parameterization.

4. **Dependency injection:** FastAPI's `Depends()` system allows clean separation of concerns: `get_db()` provides the database session, `get_current_user()` validates the JWT, and `require_role("dpo")` enforces RBAC — all composable without inheritance or global state.

5. **Type hints as the API contract:** FastAPI uses Python type hints to generate both validation logic and documentation. This forces the codebase to be fully typed (enforced by the CLAUDE.md standard: "Type hints are mandatory on all function signatures").

### Alternatives Considered
- **Flask:** Mature and widely understood. However, Flask is synchronous by default (WSGI). `flask-async` extensions exist but are not production-grade. The database-heavy nature of this API (every request hits PostgreSQL) means async is essential.
- **Django REST Framework:** Feature-rich, excellent ORM integration. However, Django's ORM is synchronous; `django-channels` or `channels` would be needed for async, significantly increasing complexity. DRF's convention-over-configuration style conflicts with the explicit, testable dependency injection approach used here.
- **Express (Node.js):** JavaScript throughout the stack would eliminate the language context switch between backend and frontend. However, the ML pipeline is entirely Python, the ETL is PySpark/Python, and the Airflow DAGs are Python — a Node.js backend would create a fourth language in the codebase.

---

## 11. Async Database Layer — SQLAlchemy 2 + asyncpg

### Decision
Use **SQLAlchemy 2.0 async** with **asyncpg** as the PostgreSQL driver (`create_async_engine`, `async_sessionmaker`, `AsyncSession`).

### Why

FastAPI's ASGI server runs an asyncio event loop. If the database driver is synchronous (psycopg2), every query blocks the event loop, defeating the purpose of async. The `asyncpg` driver is natively async and significantly faster than psycopg2:

- asyncpg implements the PostgreSQL binary protocol directly (no libpq dependency).
- Benchmark: asyncpg executes simple queries 3–4× faster than psycopg2 in async scenarios.
- SQLAlchemy 2.0's async session (`AsyncSession`) integrates cleanly: `await db.execute(text(...), params)` is non-blocking.

The `pool_pre_ping=True` configuration sends a `SELECT 1` before each connection checkout from the pool. This prevents "connection not available" errors after PostgreSQL restarts or idle timeouts — critical for a long-running API.

`expire_on_commit=False` on the session factory means ORM objects remain accessible after `await db.commit()`. Without this, accessing an attribute after commit would trigger a lazy-load, which is not supported in async mode and would raise an error.

The `DATABASE_URL` replacement logic:
```python
.replace("postgresql://", "postgresql+asyncpg://")
```
...handles the case where the environment variable uses the synchronous driver prefix (common in Heroku/Railway deployments), silently upgrading it to the async driver without requiring operators to change their env vars.

---

## 12. SQL Query Style — Raw text() with Named Parameters

### Decision
Write all database queries as raw SQL using SQLAlchemy's `text()` with named parameter binding, rather than using an ORM (Active Record / Declarative Base) or string interpolation.

```python
await db.execute(
    text("SELECT * FROM pii_findings WHERE table_id = :tid"),
    {"tid": table_id}
)
```

### Why — SQL Injection Prevention

String interpolation in SQL is the most common cause of SQL injection vulnerabilities (OWASP A03:2021). The CLAUDE.md explicitly requires: "No raw string interpolation in queries — always use parameterized queries."

Named parameters (`:tid`, `:pii_category`, `:limit`) are substituted by the database driver at the protocol level — the user-supplied value is never concatenated into the SQL string. The database treats it as data, not as SQL syntax. This prevents `'; DROP TABLE pii_findings; --` or `' OR 1=1 --` from being interpreted as SQL.

### Why Raw SQL over ORM

The risk inventory query has complex conditional GROUP BY, HAVING, and CASE WHEN logic:
```sql
HAVING :pii_category = ANY(array_agg(DISTINCT pf.pii_category))
```

Expressing this as SQLAlchemy ORM method chains would be:
1. Less readable than the SQL equivalent.
2. Harder to optimize (ORM-generated SQL can produce inefficient query plans).
3. Harder to copy into a SQL client for debugging.

Raw SQL is **more transparent** for complex analytical queries. The security benefit (parameterization) is identical to the ORM approach — both use bind parameters.

The conditional WHERE/HAVING clauses are built from **Python boolean expressions** (hardcoded strings, not user input):
```python
{"AND se.source_name ILIKE :source" if source else ""}
```
Only the Python flag (`if source`) controls which SQL fragment is included. The actual value (`source`) never enters the SQL template — it is always passed as a parameter. This pattern is safe because the branching logic is determined by the code, not by user input.

---

## 13. Database — PostgreSQL 15

### Decision
Use **PostgreSQL 15** as the single relational database for: scanner events, PII findings, column samples metadata, audit log, quarantine manifest, model registry, and the Airflow metadata store.

### Why
PostgreSQL 15 was chosen over alternatives for several specific features used in this project:

1. **`array_agg(DISTINCT ...)` and array operators:** The risk inventory query uses `array_agg(DISTINCT pf.pii_category)` to aggregate PII categories per table, and `:pii_category = ANY(array_agg(...))` for HAVING-clause filtering. This is native PostgreSQL array syntax. MySQL and SQLite do not support this idiom.

2. **JSONB columns:** `scanner_events.raw_event` and `audit_log.details_json` store JSON blobs that are queryable with GIN indexes. `::jsonb` casting enables efficient key-based lookups without deserializing to application code. MySQL's JSON support is less performant for GIN-indexed queries.

3. **`count(*) FILTER (WHERE ...)`:** The stats summary query uses conditional aggregation with `FILTER`. This is ANSI SQL but MySQL requires a more verbose `SUM(CASE WHEN ...)` equivalent.

4. **Append-only trigger enforcement:** The audit log must be append-only. A PostgreSQL trigger can enforce this at the database level, preventing even privileged application roles from issuing UPDATE or DELETE on `audit_log`. MySQL supports triggers but with more restrictive syntax.

5. **RDS PostgreSQL 15:** On AWS, RDS PostgreSQL 15 is available with Multi-AZ, automatic backups, read replicas, and encryption at rest — all required for GDPR Article 32 (security of processing).

### Alternatives Considered
- **MySQL 8:** Compatible with most queries, lacks array types and FILTER aggregation. Would require rewriting the most complex queries.
- **SQLite:** Excellent for development and testing (used in CI with `sqlite+aiosqlite:///:memory:`), but no network protocol (cannot be used by multiple containers), no array types, no concurrent write safety. Correctly used only in CI for unit tests.
- **MongoDB / DynamoDB (NoSQL):** The data model is relational: scanner events join to PII findings join to audit logs via foreign keys. A document store would require application-level joins, losing the transactional consistency guarantees needed for the append-only audit log.
- **Amazon Aurora Serverless:** PostgreSQL-compatible. Would be an acceptable alternative for production, but adds complexity (serverless v2 cold starts, connection pooling through RDS Proxy) without a clear benefit for this workload pattern.

---

## 14. Cache — Redis 7

### Decision
Use **Redis 7** as the caching and session layer, with TLS in transit and encryption at rest (ElastiCache).

### Why
Redis is used for two purposes in this system:
1. **API rate limiting** (in production, the in-memory `_buckets` dict would be replaced with a Redis-backed implementation to work correctly across multiple API pods)
2. **Airflow result backend** and celery broker (when Airflow is configured with the CeleryExecutor)

Redis 7 specifically adds:
- **ACL improvements:** Fine-grained command restrictions (restrict the Airflow broker user to only `LPUSH`, `RPOP`, `BLPOP` — preventing accidental `FLUSHALL`).
- **Functions (FUNCTION LOAD):** Server-side scripting with proper library management, replacing error-prone `EVAL` scripts.
- **Multi-AZ with ElastiCache Replication Group:** The Terraform configuration sets `num_cache_clusters = 2` for production and enables `automatic_failover_enabled = true`. If the primary node fails, ElastiCache promotes the replica in ~30 seconds — automatic, no manual intervention.

### Alternative Considered
- **Memcached:** Simpler, faster for pure key-value caching. However, Memcached does not support data structures (lists, sorted sets) needed for the rate limiter sliding window, does not support persistence, and has no replication support.
- **In-memory dict:** The current rate limiter uses an in-memory `_buckets` dict. This is correct for a single-process deployment but breaks in a horizontally scaled API pod setup (each pod has its own dict, so a client can make 10 requests to each of 3 pods = 30 total before triggering a limit). Redis solves this with atomic `ZADD`/`ZRANGEBYSCORE` operations shared across all pods.

---

## 15. Authentication — JWT with HS256 + RBAC

### Decision
Use **JSON Web Tokens (JWT)** with the **HS256 algorithm** for authentication, and a **role-based access control (RBAC)** model with four roles: `admin`, `dpo`, `auditor`, `viewer`.

### Why JWT

1. **Stateless:** The API server does not store sessions. Any API pod can validate a JWT independently by verifying the HMAC signature with the shared secret key — no shared session store required. This is essential for horizontal scaling.
2. **Self-contained:** The JWT payload carries `sub` (email), `role`, `name`, and `exp` (expiry). The API extracts the user's role from the token without a database lookup per request.
3. **Standard:** Libraries like `python-jose` implement JWT signing and verification per RFC 7519. The token format is understood by the frontend, load balancers, and any future service.

### Why HS256 and Not RS256

HS256 uses a **shared symmetric secret**. RS256 uses a **public/private key pair**. The trade-off:
- HS256: Simpler setup (one secret env var), faster verification (HMAC-SHA256 vs RSA). Adequate when the API is the only party both issuing and verifying tokens (no external authorization server).
- RS256: Required when the API issues tokens that third-party services must verify (because the private key stays secret, only the public key is shared). This system has no third-party verifiers — only the FastAPI backend issues and verifies its own tokens.

HS256 is appropriate here. The secret (`JWT_SECRET_KEY`) is rotatable via environment variable.

### Why This RBAC Design

The four roles map directly to organizational personas with distinct information-access requirements:

| Role | Access | Rationale |
|---|---|---|
| `viewer` | Read-only: risks, stats, PII report | Data analysts who need visibility but no authority to change data |
| `auditor` | viewer + audit log export | Internal/external auditors who must produce compliance evidence |
| `dpo` | auditor + remediation triggers | Data Protection Officers with legal authority to remediate |
| `admin` | dpo + system configuration | Platform engineers who manage the deployment |

The `require_role("dpo", "admin")` dependency factory enforces this at the FastAPI route level. Adding it to a route endpoint is a one-line change — there is no separate authorization service to configure.

---

## 16. Rate Limiting — In-Memory Sliding Window

### Decision
Implement rate limiting as a **sliding-window counter** using an in-memory Python `dict` (`_buckets`), exposed as a FastAPI `Depends()` function.

```python
_buckets: dict[str, list[float]] = defaultdict(list)
```

### Why In-Memory over Redis-Backed

For the **authentication endpoint**, in-memory rate limiting is deliberately chosen because:
1. **Zero dependencies for correctness:** The rate limit on the auth endpoint is a security control, not a business rule. Even if Redis is temporarily down, the rate limit must still work. An in-memory implementation is always available.
2. **Single-process acceptable for auth:** Auth rate limiting is IP-based. If multiple API pods run, an attacker gets `10 attempts × N pods`. For the current threat model (credential stuffing, not distributed attack), this is acceptable. If a distributed attacker is in scope, a Redis-backed solution would be required.
3. **No new dependency:** Adding Redis as a production dependency requires Redis in CI, in tests, and in local development. The in-memory approach works everywhere without configuration.

### Why Sliding Window over Fixed Window

A **fixed window** (reset counter at the start of each 60-second window) has a race condition: an attacker can send 10 requests at 00:59:50 and another 10 at 01:00:01, getting 20 requests in a 11-second period without triggering the limit. The **sliding window** checks the count of timestamps within the last 60 seconds from *now*, which closes this gap.

Implementation: timestamps in `_buckets[key]` are evicted when `t <= now - 60`, and the new timestamp is appended. The length check happens before the append, which is the correct order.

---

## 17. Security Headers — Starlette Middleware

### Decision
Add all security response headers via a **custom `BaseHTTPMiddleware`** class (`SecurityHeadersMiddleware`) in `api/middleware.py`.

### Why Middleware over nginx/ALB

Headers could be added by the reverse proxy (nginx, AWS ALB). We deliberately added them in the application layer:
1. **Defense in depth:** Application-layer headers work even in development (local `uvicorn`), in testing (`AsyncClient`), and behind any proxy configuration.
2. **Testable:** The 16 security tests in `tests/security/test_pii_log_audit.py` verify headers are present by making HTTP requests to the ASGI app directly. If headers were only added by nginx, the tests would only pass in a production environment.
3. **Version-controlled:** The security header policy is code (`_SECURITY_HEADERS` dict), not a nginx config file that lives outside the repo. Changes are reviewed in PRs.

**Conditional HSTS:** `Strict-Transport-Security` is only added when `X-Forwarded-Proto: https` is present. This is correct behavior:
- In development (plain HTTP), adding HSTS would break the browser's ability to load the dashboard over HTTP.
- In production (behind an HTTPS-terminating ALB), the ALB adds `X-Forwarded-Proto: https`, triggering HSTS.
- The `.setdefault()` call respects any HSTS value already set by the proxy.

---

## 18. CORS Policy — Restricted Methods and Origins

### Decision
Configure CORS to allow only `["GET", "POST", "OPTIONS"]` methods and only `http://localhost:5173` as the allowed origin in development.

```python
allow_methods=["GET", "POST", "OPTIONS"],
allow_headers=["Authorization", "Content-Type"],
```

### Why
CORS is not a server-side security control (the browser enforces it, not the server), but it prevents accidental cross-origin usage:
- **Excluding `PUT`, `PATCH`, `DELETE`:** The API has no such endpoints. Excluding them prevents any future accidental routing or proxy misconfiguration from exposing mutation endpoints to cross-origin callers.
- **Excluding wildcard `allow_origins=["*"]`:** Wildcard CORS would allow any website to make authenticated requests to the API using the user's existing session cookies. With explicit origin restriction, CORS attacks from malicious third-party sites are blocked.
- **`allow_headers` explicit list:** Only `Authorization` and `Content-Type` are needed. Allowing `*` headers would permit custom headers that could be used in header-injection attacks.

In production, the allowed origin is set to `https://dashboard.piidetect.yourcompany.com` via environment variable.

---

## 19. Privacy Logging — structlog, Metadata-Only

### Decision
Use **structlog** for the inference service with a `JSONRenderer` processor, and enforce at the code level that **raw PII values are never passed to any log call**.

```python
log.info(
    "column_classified",
    table_id=request.table_id,
    column_id=col.column_id,
    pii_category=pii_category,
    confidence=confidence,
    flagged=flagged,
)
# Note: col.values is NOT logged — only the column_id and result
```

### Why
This addresses OWASP A09:2021 (Security Logging and Monitoring Failures) and directly implements the GDPR principle of data minimization (Article 5(1)(c)).

**Why structlog over Python's built-in `logging`:**
1. **Structured output (JSON):** Each log line is a JSON object with typed fields. This enables log aggregation tools (CloudWatch, Elasticsearch, Datadog) to index individual fields. Searching for `column_id=col-007` across a million log lines is a single index lookup, not a full-text grep.
2. **Bound loggers:** `log.bind(table_id=table_id)` creates a child logger that automatically includes `table_id` in every subsequent call without repeating it. This prevents accidentally omitting context fields.
3. **Processor pipeline:** The `processors` list enforces consistent formatting: timestamp → log level → JSON render. Adding a new processor (e.g., a PII-scrubbing processor that replaces email-shaped strings with `[REDACTED]`) is a one-line addition to the pipeline.

The **`docs_url=None, redoc_url=None`** setting on the inference service FastAPI app deliberately disables the Swagger UI. This prevents operators from accidentally pasting real column samples into the Swagger "Try it out" form, which would log them in the browser's network tab.

---

## 20. Frontend Framework — React 18 + TypeScript + Vite

### Decision
Build the Privacy Risk Inventory dashboard with **React 18**, **TypeScript** (strict mode), and **Vite** as the build tool.

### Why React 18
1. **Component model:** The dashboard is a complex single-page application with multiple pages (Risk Inventory, PII Report, Audit Log, Data Sources), shared state (auth token, current filters), and dynamic data loading. React's component model is well-suited: each page is a tree of independent, reusable components.
2. **Concurrent features:** React 18's `useTransition` and `Suspense` allow the dashboard to remain responsive while large data tables load — showing skeleton placeholders without blocking the entire UI.
3. **Ecosystem:** TanStack Query, Zustand, and Recharts (used for the heatmap) all have first-class React integrations.

### Why TypeScript Strict Mode
The CLAUDE.md standard requires `"strict": true` in tsconfig. This enables:
- `noImplicitAny`: Every variable must have a declared type, preventing runtime type errors.
- `strictNullChecks`: `null` and `undefined` must be explicitly handled. The `token: string | null` pattern in `authStore.ts` correctly models the unauthenticated state.
- `useQuery<RisksResponse>`: The generic type parameter means TypeScript verifies that the `body.items` access is safe at compile time.

Strict TypeScript eliminated an entire class of bugs discovered in Sprint 6: the `useLogin` hook was initially setting `email: ''` (empty string) because the API response didn't include the email. TypeScript caught the missing field at compile time before the bug reached the browser.

### Why Vite over Create React App (CRA)
- **Build speed:** Vite uses native ES modules in development (no bundling during dev server) and esbuild for the production build. Cold start: ~300ms vs. CRA's ~10 seconds.
- **HMR (Hot Module Replacement):** Vite's HMR is component-granular — changing a CSS value updates the component in ~50ms without a page reload. This dramatically accelerates UI development against the BRAND.md design system.
- **Modern:** CRA is in maintenance mode (no longer actively developed). Vite is the current standard for new React projects.

### Why Not Next.js
Next.js adds server-side rendering (SSR) and file-system routing. These features are not needed here:
- The dashboard is not indexed by search engines (it requires authentication).
- SSR would complicate the JWT auth flow (tokens cannot be read server-side without cookies, which adds CSRF concerns).
- The API already handles data fetching; Next.js's data fetching model (getServerSideProps) would create a redundant intermediary.

---

## 21. State Management — Zustand

### Decision
Use **Zustand** for global state management, specifically for the authentication state (`token`, `user`, `setAuth`, `logout`).

### Why Zustand over Redux

Redux requires boilerplate: action types, action creators, reducers, and a store configuration. For managing a single authentication state object with two operations (`setAuth`, `logout`), Redux would add ~150 lines of boilerplate for no architectural benefit.

Zustand implements the same pattern in 32 lines (`authStore.ts`) with:
- A single `create<AuthState>()` call that defines state and actions together.
- No reducers, no dispatch, no action creators.
- Selector functions (`useAuthStore((s) => s.token)`) that re-render only when the selected slice changes — equivalent to `connect()` with `mapStateToProps` in Redux.

### Why Not React Context

React Context is the built-in state solution. For `AuthState`, it would work correctly. However, Context has a known performance limitation: every component that calls `useContext(AuthContext)` re-renders whenever **any** value in the context changes. In a dashboard with 10+ components reading the auth state, this causes unnecessary re-renders on every page navigation.

Zustand's selector pattern (`useAuthStore((s) => s.token)`) re-renders only the component whose selected value changed. This is the primary architectural advantage for a complex SPA.

---

## 22. Data Fetching — TanStack Query v5

### Decision
Use **TanStack Query v5** (`@tanstack/react-query`) for all server-state management: `useQuery` for GET endpoints, `useMutation` for the remediate POST.

### Why
TanStack Query solves five problems that raw `fetch` + `useState` cannot:

1. **Deduplication:** If `useRisks` is mounted in both the sidebar and the main panel, TanStack Query makes exactly one network request, not two.
2. **Stale-while-revalidate:** The risk inventory is shown immediately from cache, then silently refreshed in the background. Users see data instantly; they see fresh data within seconds.
3. **Loading and error states:** `{ data, isLoading, error }` destructuring replaces three separate `useState` calls per endpoint.
4. **Cache invalidation:** After a remediation POST succeeds, `queryClient.invalidateQueries(['risks'])` triggers an automatic refetch of the risk inventory. The DPO sees the updated status without a page reload.
5. **Query key-based caching:** `queryKey: ['risks', qs]` means every unique filter combination has its own cache entry. Navigating back to a previously visited filter set shows cached data instantly.

Version 5 specifically changed `onSuccess`/`onError` from callbacks to use the mutation object's result, which resolved the empty-email bug in `useLogin` (where `variables.email` is correctly available in the v5 pattern).

---

## 23. Token Storage — sessionStorage over localStorage

### Decision
Store the JWT in **`sessionStorage`** rather than `localStorage` or an HTTP-only cookie.

```typescript
sessionStorage.setItem('pii_token', token);
```

### Why Not localStorage
`localStorage` persists across browser sessions. If a user walks away from a shared workstation without logging out, the next person who opens the browser tab can access the dashboard with the previous user's permissions. For a system that exposes PII risk data, this is unacceptable.

`sessionStorage` is scoped to the browser tab. Closing the tab destroys the session. This matches the security expectation: "I logged out by closing the browser."

### Why Not HTTP-Only Cookie
HTTP-only cookies are immune to XSS attacks (JavaScript cannot read them). This is the most secure option for token storage. However, HTTP-only cookies require:
1. The API and frontend to share the same domain (or careful CORS+credentials configuration).
2. CSRF protection (anti-CSRF tokens), since cookies are sent automatically on cross-origin requests.

The current architecture (frontend on `dashboard.domain.com`, API on `api.domain.com`) would require both a cookie domain configuration and CSRF middleware — significant added complexity. The OWASP recommendation is HTTP-only cookies for production systems handling highly sensitive data. This is a **known trade-off** documented here: the current sessionStorage implementation is acceptable for MVP, with HTTP-only cookies as the recommended production hardening step.

### XSS Mitigation
Since the token is in sessionStorage (accessible to JavaScript), XSS is the primary attack vector. XSS is mitigated by:
- `Content-Security-Policy` header (restricts script sources).
- `X-XSS-Protection: 1; mode=block` header.
- React's default HTML escaping (all user-provided strings rendered via JSX are automatically HTML-escaped).

---

## 24. Quarantine Zone — S3 Prefix Isolation

### Decision
Quarantine flagged data by **moving** (not copying) it to `s3://pii-quarantine/pending/{table_id}/`, a separate S3 bucket with a restricted IAM policy.

### Why Move, Not Delete
The system cannot immediately delete data for several reasons:
1. **GDPR Article 17 (Right to Erasure) requires documentation:** Deletion must be evidenced. The DPO must be able to confirm that data was deleted, what data it was, and when. Quarantine creates this evidence trail in `quarantine_manifest` before deletion.
2. **Legal hold:** Regulatory investigations may require data to be preserved. Moving to quarantine allows a "legal hold" flag to prevent the 30-day auto-delete.
3. **False positive recovery:** If the model incorrectly flags a non-PII table, quarantine allows the DPO to release the data. Immediate deletion is irreversible.

### Why a Separate Bucket, Not a Prefix in the Same Bucket
S3 bucket policies are the access control unit. A separate bucket allows:
- A bucket policy that denies all `s3:GetObject` calls except from the `DPO` IAM role.
- CloudTrail data events on the quarantine bucket specifically, creating an audit trail of every access attempt.
- Independent lifecycle rules on the quarantine bucket without affecting the data lake bucket.

### Why Not a Separate AWS Account
For maximum isolation, quarantined data could live in a separate AWS account. This would completely prevent cross-account access mistakes. However, it introduces:
- Cross-account IAM role assumption complexity (every quarantine write would require `sts:AssumeRole`).
- Separate billing, monitoring, and alerting setup.
- Significantly higher operational complexity for a team that is already managing multiple services.

The single-account, separate-bucket approach is the industry-standard middle ground.

---

## 25. Data Retention — 30-Day Quarantine Window

### Decision
Automatically delete quarantined data after **30 days**, with a **7-day warning** at day 23.

### Why 30 Days
This maps directly to regulatory requirements:
- **GDPR Article 17:** Controllers must erase personal data without undue delay when the legal basis for processing ceases. "30 days" is a commonly accepted interpretation of "without undue delay" for operational remediation workflows.
- **LGPD Article 6(X) (Non-Retention):** Data must be eliminated when the purpose of collection ends. Quarantine is not a storage purpose — it is a temporary holding state pending DPO decision.

Shorter windows (7 days) would not give DPOs enough time to review findings, especially during vacations or peak compliance periods. Longer windows (90 days) would accumulate PII data in the quarantine bucket in excess of what is needed.

### Why the Two-Phase Architecture (S3 Lifecycle + DAG)

S3 lifecycle rules operate at the object metadata level with no application awareness — they cannot send emails or write to the audit log. The `dag_quarantine_expiry` DAG handles the application-layer concerns (warnings, DB updates, audit records), while the S3 lifecycle rule serves as a **safety net** for the actual deletion. If the DAG fails, the lifecycle rule still deletes the object at day 30 (via the `expired/` prefix). Defense in depth at the infrastructure level.

---

## 26. Append-Only Audit Log

### Decision
The `audit_log` table has **no UPDATE or DELETE operations permitted**, enforced at the database level via a PostgreSQL trigger.

### Why
The audit log is the legal evidence trail. For an auditor or regulator to trust the log, it must be tamper-evident. If application code (or an attacker who compromises the application's database user) could delete audit records, the log would be meaningless as compliance evidence.

The PostgreSQL trigger approach enforces this at the database layer, independent of application code. Even if a developer accidentally writes `session.execute(text("DELETE FROM audit_log WHERE ..."))`, the trigger raises an exception before the statement executes.

This directly implements CLAUDE.md constraint #3: "Audit trail is append-only — no UPDATE or DELETE on `audit_log` table."

GDPR Article 30 (Records of Processing Activities) and LGPD Article 37 both require evidence of data processing decisions to be maintained. A mutable audit log is not compliant evidence.

---

## 27. Idempotency in All DAGs

### Decision
Every Airflow DAG is **idempotent**: running it multiple times with the same inputs produces the same result.

### Why
Airflow's execution model guarantees at-least-once delivery — a DAG run can be retried automatically on failure. If a network timeout occurs mid-remediation, the DAG will restart from the failed task. Without idempotency:
- A table could be anonymized twice, corrupting already-anonymized values.
- An audit log entry could be written twice, creating duplicate records.
- A DPO warning email could be sent multiple times for the same quarantine entry.

**Implementation patterns used:**

1. **Upsert on scanner events:** `upsert_scanner_event()` uses PostgreSQL `INSERT ... ON CONFLICT (event_id) DO UPDATE`, so re-processing the same Kafka message has no effect.

2. **Anonymization strategies check existing format:** `_is_already_hashed(value)` in `strategies.py` detects values already in SHA-256 format and passes them through. Re-running `spark_anonymizer.py` on an already-anonymized table is a no-op.

3. **Warning sent check:** `dag_quarantine_expiry` queries `WHERE warning_sent_at IS NULL` — a second run the same day will find no rows to warn about, sending no duplicate emails.

---

## 28. Sampling — Maximum 1,000 Rows

### Decision
Sample a maximum of **1,000 random rows** per column for inference input.

### Why 1,000 Specifically
This is a deliberate precision-versus-privacy trade-off:
- **Statistical sufficiency:** For a column with a PII prevalence of 50%, a sample of 1,000 rows gives a margin of error of ±3.1% at 95% confidence (by the standard proportions formula). For PII prevalence of 90% (a column that is almost entirely emails), even 50 rows is statistically conclusive. 1,000 rows is sufficient for confident classification across all prevalence rates.
- **Privacy minimization:** GDPR Article 5(1)(c) requires "data minimisation" — collecting only what is necessary. Sending 1,000,000 rows to the inference service when 1,000 rows provide the same classification result is a data minimization violation. 1,000 is the principled minimum that is still statistically sound.
- **Inference latency:** The p95 < 2s SLA for the inference service is achieved at 1,000 samples per column. At 10,000 samples, inference would take 20+ seconds per column batch.

The limit is configurable, not hardcoded: `max_samples=10` in `classify_column()` refers to the samples passed to the BERT tokenizer per call (for batching efficiency); the outer 1,000-row limit is set in the sampling DAG.

---

## 29. Infrastructure — AWS over Other Clouds

### Decision
Deploy all infrastructure on **Amazon Web Services**, using S3, EKS, RDS, ElastiCache, MSK, Route53, ACM, and IAM.

### Why
The primary data sources being scanned are already on AWS (S3 data lake, AWS Glue catalog, Amazon Redshift). Running the detection pipeline on the same cloud provider:
1. **Eliminates cross-cloud egress costs:** Transferring terabytes of column samples from S3 to GCP Compute for inference would incur AWS egress costs of ~$0.09/GB. Running inference on EKS in the same region is free for intra-VPC traffic.
2. **Simplifies IAM:** The Airflow workers can assume an IAM role with S3 read access to the data lake. Cross-cloud authentication (e.g., GCP Workload Identity Federation with AWS) adds significant complexity.
3. **MSK + S3 + EKS in one region:** All services are in `us-east-1`, ensuring sub-millisecond latency between Kafka brokers, the scanner consumers, and the PostgreSQL database.

---

## 30. Compute — EKS over ECS or Lambda

### Decision
Run all application services (API, Dashboard, Inference) on **Amazon EKS** (Kubernetes).

### Why EKS over ECS

ECS (Elastic Container Service) is simpler to operate but:
- Uses proprietary task definition JSON (not portable across clouds or to on-premise).
- Has limited HPA (Horizontal Pod Autoscaler) equivalent — ECS service auto-scaling requires CloudWatch Alarms and target tracking policies, which are less flexible than Kubernetes HPA.
- Helm charts (chosen for the deployment model) are Kubernetes-native and do not have an ECS equivalent.

EKS allows the blue/green inference deployment to be implemented with a Kubernetes Service selector flip — a concept that does not exist in ECS without an ALB-level weighted routing rule.

### Why Not Lambda

Lambda's maximum execution time is 15 minutes. The inference service model load takes ~30–60 seconds. A cold-started Lambda would spend 30–60 seconds loading the model before processing the first request — exceeding the p95 < 2s SLA for the first request of each new Lambda instance. Provisioned Concurrency solves this but costs as much as a running EKS node while eliminating the cost advantage of serverless.

The inference service also requires ~4GB RAM for the DistilBERT model (weights + runtime). Lambda's memory maximum is 10GB, so technically feasible, but at a cost premium.

---

## 31. Managed Kafka — MSK over Self-Managed

### Decision
Use **Amazon MSK** (Managed Streaming for Apache Kafka) rather than self-managing Kafka on EC2.

### Why
Kafka cluster management is operationally complex: broker patching, ZooKeeper (pre-3.x) or KRaft (3.x) quorum management, log compaction tuning, rebalancing on broker failure. MSK manages all of this:
- Automatic broker replacement on failure.
- Multi-AZ placement by default (each broker in a separate AZ, as configured in Terraform: `number_of_broker_nodes = length(var.availability_zones)` = 3).
- Integrated CloudWatch metrics for consumer lag.
- SASL/SCRAM authentication managed via AWS Secrets Manager.

The Terraform MSK configuration enforces `client_broker = "TLS"` and `in_cluster = true` encryption — satisfying CLAUDE.md constraint #4: "All data in transit encrypted — TLS 1.2+ for Kafka."

---

## 32. Database HA — RDS Multi-AZ for Production

### Decision
Enable **Multi-AZ** for the RDS PostgreSQL instance in production only (`multi_az = var.environment == "production"`).

### Why Conditional Multi-AZ
Multi-AZ doubles the RDS instance cost. In development and staging, data can be restored from a snapshot; downtime is acceptable. In production, the audit log and risk inventory are mission-critical for DPO operations — PostgreSQL unavailability means DPOs cannot review or remediate findings.

Multi-AZ provides **synchronous replication** to a standby instance in a different Availability Zone. If the primary fails, RDS automatically promotes the standby (typically 60–120 seconds). No data loss (RPO = 0) and minimal downtime (RTO ≈ 2 minutes).

The `deletion_protection = var.environment == "production"` flag prevents accidental `terraform destroy` from deleting the production database — a one-line Terraform safeguard.

---

## 33. Infrastructure-as-Code — Terraform

### Decision
Use **HashiCorp Terraform** (version ≥ 1.7.0) for all AWS infrastructure provisioning.

### Why Terraform over AWS CDK or CloudFormation

1. **Language-agnostic HCL:** Terraform's HashiCorp Configuration Language is purpose-built for infrastructure. It is declarative, has clear resource graph semantics, and its `plan` output shows exactly what will be created/destroyed before applying — a critical safety property when managing production databases.

2. **`terraform plan` as a PR artifact:** The PLAN.md explicitly requires: "Infrastructure changes (Terraform) require a `terraform plan` output attached to the PR." CloudFormation change sets and CDK diffs are less readable for reviewers who are not deep CloudFormation experts.

3. **State management:** Terraform's remote state (S3 + DynamoDB lock, configured in `backend.tf`) allows the entire team to share infrastructure state and prevents concurrent `apply` operations from conflicting.

4. **Provider ecosystem:** Terraform's AWS provider (`~> 5.50`) covers all AWS services used: `aws_eks_cluster`, `aws_msk_cluster`, `aws_elasticache_replication_group`, etc. — all with first-class support.

### Alternative: AWS CDK
CDK expresses infrastructure as TypeScript/Python code. This is attractive for developers already writing TypeScript/Python. However:
- CDK is a CloudFormation generator — the underlying infrastructure is CloudFormation stacks, which have a 500-resource limit per stack. Large deployments require StackSets, which add significant complexity.
- Terraform's plan is more readable than CloudFormation diffs for reviewers unfamiliar with CDK constructs.

---

## 34. Deployment Strategy — Blue/Green for Inference

### Decision
Use a **blue/green deployment strategy** for the PII Inference service, implemented as two parallel Kubernetes Deployments (`pii-inference-blue`, `pii-inference-green`) with a single production Service whose `selector` points to the active slot.

### Why Blue/Green Specifically for Inference

The inference service hosts the ML model. Model updates are the most disruptive change in the system:
- A new model version may have different output probabilities for the same inputs, causing a temporary inconsistency if some requests go to the old model and some to the new model (split-brain classification).
- The model takes 30–60 seconds to load in memory. A rolling deployment (replacing pods one by one) would expose some users to cold-start latency during the transition.

Blue/green solves both problems:
- The new model is fully loaded in the green slot before any production traffic is shifted (smoke-test via the preview Service).
- Traffic is shifted atomically (Service selector update = O(1) operation, no new pod restarts). No client sees a slow cold-start response.
- Rollback is equally atomic: switch the selector back to blue in ~30 seconds.

### Why Not Rolling Deployment (Standard Kubernetes)
Rolling deployment (`strategy: RollingUpdate`) replaces pods one at a time. At any point during the rollout, some pods run the old model and some run the new one. For PII classification, this means:
- The same table scanned at 14:00 might be classified by the old model (EMAIL, confidence=0.87).
- A rescan at 14:05 might be classified by the new model (EMAIL, confidence=0.92).
- The audit log shows two different confidence scores for the same finding — confusing for DPOs and auditors.

### Why Not Canary
Canary deployment sends a small percentage of traffic to the new version. While appropriate for APIs where requests are stateless and independent, canary is problematic for the inference service: a PII misclassification by the new model (even at 5% traffic) could cause a live table to be incorrectly flagged or incorrectly cleared. The blast radius must be zero until the model is validated.

---

## 35. Helm Umbrella Chart over Kustomize

### Decision
Use **Helm** charts for Kubernetes deployment configuration, with an umbrella chart (`pii-ghost-hunter`) and a separate blue/green chart (`pii-inference-bluegreen`).

### Why Helm over Kustomize
Kustomize manages Kubernetes configurations through overlays and patches. It is built into `kubectl` and requires no additional installation. However:
- Helm's **values-based templating** (`values.yaml`, `values.production.yaml`) provides a cleaner interface for environment-specific configuration than Kustomize's patch files.
- Helm's `upgrade --install` command is idempotent: it creates the resources if they don't exist, and updates them if they do. Kustomize requires separate `apply` and checking logic.
- The `helm upgrade ... --set api.image.tag=${{ github.sha }}` pattern in the deploy workflow passes the exact Git SHA as the image tag — a clean, auditable deployment record.
- Helm's release history (`helm history pii-ghost-hunter`) shows every deployment with its values — this is the audit trail for production changes.

---

## 36. CI/CD — GitHub Actions

### Decision
Use **GitHub Actions** for all CI (lint, test, security scan) and CD (build, push, deploy) pipelines.

### Why
The codebase is hosted on GitHub. GitHub Actions is natively integrated:
1. **No external CI server:** No Jenkins, CircleCI, or GitLab instance to maintain. The CI pipeline is defined as YAML files in `.github/workflows/` — versioned in the same repository as the code.
2. **OIDC authentication to AWS:** The deploy workflow uses `aws-actions/configure-aws-credentials@v4` with `role-to-assume`, which uses GitHub's OIDC identity provider to assume an AWS IAM role without storing long-term credentials in GitHub Secrets. This is the current best practice for CI/CD AWS authentication.
3. **Concurrency control:** `cancel-in-progress: true` for CI ensures that a new push to a PR branch immediately cancels the still-running CI for the previous push. This prevents "ghost" CI runs from blocking merge.
4. **`concurrency: cancel-in-progress: false`** for the deploy workflow is the opposite decision: a deploy in progress must never be interrupted by a new push, as a partial deploy could leave the cluster in an inconsistent state.
5. **GitHub Environments:** The `environment: production` declaration on the deploy job triggers GitHub's environment protection rules (required reviewers, deployment branches), providing a mandatory human approval gate before any production deployment.

---

## 37. Monitoring — Prometheus + Grafana

### Decision
Use **Prometheus** for metrics collection and **Grafana** for dashboards. Alert rules are defined as Kubernetes-native `PrometheusRule` CRDs.

### Why Prometheus over CloudWatch
CloudWatch is AWS-native and "free" (metrics are included in most AWS service costs). However:
- Prometheus metrics are **pull-based**: the Prometheus server scrapes `/metrics` endpoints from all services. This works identically in development (local Prometheus), staging (EKS), and production (EKS) — no code change required.
- The inference service already exposes Prometheus metrics (`prometheus_client` library): `INFERENCE_LATENCY` histogram, `INFERENCE_REQUESTS` counter, `FLAGGED_COLUMNS` counter. These are unavailable in CloudWatch without a CloudWatch EMF (Embedded Metric Format) wrapper.
- `PrometheusRule` CRDs (Kubernetes-native alert rules) are version-controlled in the repository alongside the application code. CloudWatch Alarms are defined in CloudFormation or the AWS console — not version-controlled.
- **Grafana's PromQL:** The production dashboard uses PromQL expressions like `histogram_quantile(0.95, rate(...))` for SLA visualization. CloudWatch Metrics Insights has a similar but less expressive query language.

---

## 38. Testing Strategy — Unit + Security + E2E + Load

### Decision
Four distinct test layers, each with a specific scope and execution environment:

| Layer | Location | Tools | When |
|---|---|---|---|
| Unit | `tests/api/`, `tests/ml/`, `tests/etl/` | pytest, AsyncMock | Every PR |
| Security | `tests/security/` | pytest, httpx ASGI | Every PR |
| E2E | `tests/e2e/` | pytest, testcontainers | Nightly |
| Load | `tests/perf/` | Locust | Manual / pre-release |

### Why This Separation

**Unit tests** run against mocked databases (`AsyncMock + MagicMock` pattern). They test business logic in isolation: "given these DB rows, does the API return the correct JSON?" They must run fast (< 30 seconds) on every PR.

**Security tests** (`test_pii_log_audit.py`) are unit tests with a security focus. They use the real FastAPI ASGI app but mock the database. They test properties that cannot be verified by code review alone: "does the rate limiter actually return 429 on the 11th request?" "does every response carry the security headers?" These run on every PR because a code change to `middleware.py` could silently break a security guarantee.

**E2E tests** use a real PostgreSQL container (testcontainers). They validate the complete vertical slice: seed data → API query → correct response → remediation → audit log written. These are slow (container startup + migrations + multiple API calls) and require Docker, so they run nightly rather than on every PR.

**Load tests** (Locust) validate the SLA (`p95 < 500ms` for API, `p95 < 2s` for inference) under realistic concurrent load (500 users). They cannot run in automated CI because they require a running stack. They are run manually before each production release.

### The MockDB Pattern

The `AsyncMock + MagicMock` pattern used in all unit tests:
```python
result = MagicMock()           # synchronous result object
result.fetchall.return_value = []
session = AsyncMock()          # async session
session.execute.return_value = result
```

This correctly models SQLAlchemy 2.0 async behavior:
- `await session.execute(...)` returns a synchronous result object (not a coroutine).
- `result.fetchall()` is synchronous (not awaited).
- Using `AsyncMock` for `result` would cause `result.fetchall()` to return a coroutine, which would crash when iterated in the router.

This subtle distinction was discovered during Sprint 6 testing and is documented as a known pattern in the project.

---

## 39. Integration Testing — Testcontainers

### Decision
Use **testcontainers** (`testcontainers[postgresql]`) for E2E tests that require a real PostgreSQL database.

### Why
The alternative is to maintain a persistent test database (either a shared staging DB or a local `docker-compose.yml` that must be running before tests). Both approaches have problems:
- **Shared staging DB:** Tests are not isolated. Test A that writes data affects Test B's assertions. Tests cannot run in parallel. Running tests against staging is a production risk if a test has a bug.
- **Local docker-compose:** Developers must remember to start the DB before running tests. CI must maintain a persistent service container. Tests are not reproducible if the DB has stale state from a previous run.

Testcontainers solves all of these: it spins up a real PostgreSQL 15 Alpine container at test session start, runs Alembic migrations against it (`apply_migrations` fixture), runs all E2E tests, and tears it down. The DB is ephemeral, isolated, and identical every time.

Module scope (`scope="module"`) is used rather than per-test scope to amortize the container startup cost (~5 seconds) across all E2E tests in the module.

---

## 40. Load Testing — Locust

### Decision
Use **Locust** for load testing the API and Inference service.

### Why Locust over JMeter or k6
- **Locust tests are Python code:** `APIUser`, `DPOUser`, and `InferenceUser` are Python classes with `@task`-decorated methods. This means the load test logic can use the same authentication flow (POST to `/auth/token`, extract JWT, add `Authorization` header) as the real application, without XML configuration files.
- **The `@events.quitting.add_listener` pattern** (used in `assert_sla()`) allows the Locust process to exit with a non-zero code if SLA targets are breached. This makes load tests machine-verifiable: `locust --headless; echo $?` returns 1 if p95 > 500ms.
- **Weighted tasks:** `@task(5)` on `get_risks` and `@task(1)` on `health_check` models the realistic request distribution (most requests are to the inventory, few are health checks). JMeter requires XML thread group configuration for this.

---

## 41. Migrations — Alembic

### Decision
Use **Alembic** for all database schema migrations.

### Why
Alembic is the standard migration tool for SQLAlchemy. It provides:
- **Version-controlled migration files:** Each migration is a numbered Python file with `upgrade()` and `downgrade()` functions. The migration history is in `git log`.
- **Auto-generate from models:** `alembic revision --autogenerate` compares the SQLAlchemy models to the current database schema and generates the migration diff — eliminating hand-written `ALTER TABLE` statements.
- **Used in E2E tests:** The `apply_migrations` fixture calls `alembic.command.upgrade(cfg, "head")` against the testcontainers PostgreSQL database. This tests that the migration itself works correctly, not just the application logic.

All migrations are required by CLAUDE.md: "All migrations via Alembic."

---

## 42. Repository Layout — Monorepo

### Decision
All components (scanner, orchestration, ML, ETL, API, dashboard, infrastructure) live in a **single repository**.

### Why
The alternative is polyrepo (one repository per service). Polyrepo is appropriate when teams are large and independently deploy their services. For this project:
1. **Cross-service changes are common:** Adding a new PII category requires changes in `ml/data/pii_dataset.py` (labels), `api/schemas/risks.py` (enum), `dashboard/src/hooks/useRiskInventory.ts` (filter), and `BRAND.md` (color). A monorepo makes this a single PR with all changes atomic.
2. **Shared code:** `scanner/schemas/events.py` defines Pydantic schemas used by both the scanner consumers and the Airflow operators. A monorepo makes this import trivial.
3. **Single CI:** One GitHub Actions workflow validates the entire system. In polyrepo, coordinating compatible versions across repositories requires a versioning contract.
4. **CLAUDE.md is authoritative for the whole system:** A single `CLAUDE.md` at the root defines the coding standards, privacy rules, and constraints for every component. In polyrepo, this would need to be duplicated and kept in sync.

---

## 43. Python Standards — ruff + black + type hints

### Decision
Use **ruff** as the linter, **black** as the formatter, and **mandatory type hints** on all Python function signatures.

### Why ruff over flake8 + isort + pylint

ruff replaces flake8, isort, and dozens of plugins with a single binary written in Rust. It runs 10–100× faster than flake8 on the same rule set. On the CI step "ruff check .", this means lint output appears in ~2 seconds rather than 20 seconds for a codebase of this size. The `pyproject.toml` selects rules: `E` (pycodestyle errors), `W` (warnings), `F` (pyflakes), `I` (isort), `B` (bugbear), `C4` (comprehensions), `UP` (pyupgrade).

### Why black over autopep8

black is opinionated and non-configurable (line length aside). This means there is no debate about formatting style: black decides. The CI `black --check .` step fails if any file would be reformatted by black. This enforces consistent formatting across all contributors without discussion.

### Why Mandatory Type Hints

Type hints at all function boundaries serve as machine-checked documentation. They enable:
1. **mypy / pyright:** Static analysis catches type errors before runtime. If a function expects `dict[str, str]` and code passes `dict[str, Any]`, the static analyzer flags it.
2. **IDE autocomplete:** VSCode and PyCharm provide accurate autocomplete on typed function return values.
3. **FastAPI:** FastAPI uses type hints to auto-generate Pydantic validators for request/response bodies. Without type hints, FastAPI's automatic validation does not work.
4. **Living documentation:** `def create_access_token(data: dict[str, Any]) -> str` communicates the contract without a docstring.

---

*This document was written during Sprint 8 (M6 milestone) of the Shadow Data & PII Ghost-Hunter project. It covers all engineering decisions from Sprint 0 (foundation) through Sprint 8 (production rollout). Future architectural decisions should be appended here with the same level of specificity.*
