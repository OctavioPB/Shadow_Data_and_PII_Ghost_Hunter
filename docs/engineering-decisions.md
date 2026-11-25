# Engineering Decisions

A short log of the technical decisions taken during the 2026 audit and hardening pass (branch `audit/fixes`). Each entry gives the context, the decision, and its consequences.

These entries add to the original design record in [planning/DECISIONS.md](../planning/DECISIONS.md), which still explains the core technology choices (Kafka, Airflow, DistilBERT, PySpark, FastAPI, PostgreSQL, EKS, Terraform, and others). Where an entry below replaces something there, it says so.

| ID | Decision | Status |
|---|---|---|
| [ED-01](#ed-01-remediation-steps-run-sequentially-through-a-staging-prefix) | Remediation runs sequentially through a staging prefix | Accepted |
| [ED-02](#ed-02-automatic-remediation-is-limited-to-s3-objects-of-known-format) | Automatic remediation only for S3 objects of known format | Accepted |
| [ED-03](#ed-03-findings-are-unique-per-scanner-event-and-column) | Findings are unique per (event, column); writes are upserts | Accepted |
| [ED-04](#ed-04-pyjwt-replaces-python-jose) | PyJWT replaces python-jose | Accepted, replaces DECISIONS §15 library choice |
| [ED-05](#ed-05-one-requirements-file-per-component-each-audited-separately) | One requirements file per component, each audited | Accepted |
| [ED-06](#ed-06-stay-on-numpy-1x-for-now) | Stay on NumPy 1.x for now | Accepted, temporary |
| [ED-07](#ed-07-full_name-pseudonymisation-is-not-value-idempotent) | FULL_NAME is not value-idempotent | Accepted |
| [ED-08](#ed-08-airflow-tests-run-inside-the-airflow-image) | Airflow tests run inside the Airflow image | Accepted |
| [ED-09](#ed-09-mlflow-transformers-flavor-for-model-logging) | mlflow transformers flavor for model logging | Accepted |
| [ED-10](#ed-10-raise-coverage-with-tests-not-by-lowering-the-gate) | Raise coverage with tests, not by lowering the gate | Accepted |
| [ED-11](#ed-11-slim-hardened-runtime-images) | Slim, hardened runtime images | Accepted |
| [ED-12](#ed-12-keyed-hmac-for-dsar-identifiers) | Keyed HMAC for DSAR identifiers | Accepted |

---

## ED-01: Remediation steps run sequentially through a staging prefix

**Context.** Anonymization and quarantine ran in parallel on the same S3 path. The anonymizer also wrote over its own input. Depending on timing, raw data could be quarantined after being overwritten, or un-anonymized data could stay in place.

**Decision.** The steps run in this order: `anonymize` → `quarantine` → `promote` → `notify` → `audit`.
- The anonymizer writes to a staging prefix, in the source format, and refuses to write to its own input path.
- `quarantine` runs even when `anonymize` fails, using `trigger_rule=all_done`.
- `promote` runs only if both earlier steps succeeded.
- `notify` and `audit` always run. The audit event is `remediation_failed` when any step failed.

**Consequences.** Remediation takes longer, which is irrelevant on a daily cadence. Raw PII is always contained, and partial output is never promoted.

## ED-02: Automatic remediation is limited to S3 objects of known format

**Context.** The sampling DAG triggered remediation for every flagged source, but passed no path, format or columns. Remediation could only send a notification.

**Decision.** `sampling_pipeline` passes `source_s3_path`, `source_format`, `flagged_columns` and `owner_email`, and only for S3 objects in parquet, csv or json. Catalog (Glue/Athena) tables are reported to the DPO. Business rationale: [business-decisions.md BD-02](business-decisions.md#bd-02-automatic-remediation-only-where-it-is-safe).

**Consequences.** Catalog findings need manual follow-up.

## ED-03: Findings are unique per scanner event and column

**Context.** Airflow retries re-ran classification and inserted duplicate `pii_findings`, which inflated dashboard counts.

**Decision.** Migration `0008` removes the existing duplicates and adds a unique constraint on `(scanner_event_id, column_name)`. The classifier operator upserts against it. Trigger tasks treat `DagRunAlreadyExists` as success.

**Consequences.** Every DAG can be retried safely. The migration is destructive only for exact duplicates; the most recent row is kept.

## ED-04: PyJWT replaces python-jose

**Context.** python-jose depends on `ecdsa`, which has an advisory with no fix.

**Decision.** Use `PyJWT[crypto]` 2.15. Keep HS256 with the algorithm pinned on decode. Compare passwords in constant time (`hmac.compare_digest`). When `JWT_SECRET_KEY` is empty, fall back to the development key and log a warning.

**Consequences.** The token format is unchanged, so existing clients are unaffected. Expired, wrong-key, `alg=none` and malformed tokens were verified to return 401.

## ED-05: One requirements file per component, each audited separately

**Context.** A single `requirements.txt` mixed the API, Spark, ML and training stacks. mlflow 2.13 pinned `pyarrow<16`, which held the whole platform on a vulnerable pyarrow. Airflow providers required SQLAlchemy < 2, so pip-audit could not even resolve the file.

**Decision.** Split the dependencies into:
- `requirements.txt` (core);
- `api/requirements.txt` (API image);
- `requirements-ml.txt`;
- `requirements-train.txt` (ML + mlflow + accelerate);
- `requirements-dev.txt`.

Airflow providers come from the Airflow image. CI runs one blocking pip-audit step per file. The ML audit steps do **not** use the PyTorch index: pip-audit silently skips the `+cpu` build, so torch would never be audited.

**Consequences.** Images carry only what they run, and an audit failure points at one stack. The cost is that versions shared between files must be kept in step by hand.

## ED-06: Stay on NumPy 1.x for now

**Context.** torch 2.3 failed at runtime with NumPy 2. pyarrow ≥ 26 requires NumPy 2. pip does not catch this; it fails only at import time.

**Decision.** Pin `numpy==1.26.4`, `pandas==2.2.2` and `pyarrow==23.0.1`. pyarrow 23.0.1 is the newest release without known advisories that still supports NumPy 1.x.

**Consequences.** torch 2.15 now supports NumPy 2, so the three packages can move to 2.x together in a separate change. That needs a full pipeline re-test.

## ED-07: FULL_NAME pseudonymisation is not value-idempotent

**Context.** Every other strategy recognises its own output (a hash, `****-1234`, `[REDACTED]`) and leaves it unchanged on a second pass. A pseudonymised name looks like a real name.

**Decision.** Do not recognise pseudonyms by their shape. Any rule that would let a pseudonym through would also let real names through un-anonymized. Idempotency is enforced at the job level instead: a remediation does not re-process a source it has already completed.
- `test_full_name_idempotent` is a strict xfail that documents the trade-off.
- A separate test checks the property that matters: **no real name is ever returned unchanged**.

**Consequences.** Running the strategy twice on the same name yields a different pseudonym. If the xfail ever starts passing, CI fails so the trade-off gets reviewed.

## ED-08: Airflow tests run inside the Airflow image

**Context.** Airflow 2.8 requires SQLAlchemy < 2, and the API needs SQLAlchemy 2. A `container:` job ran as root, and pip in the Airflow image refuses to run as root.

**Decision.** A dedicated `airflow-test` job uses `docker run` on `apache/airflow:2.8.1-python3.11`, as the image's default user, with the repository mounted read-only. It runs a DagBag import check and then `tests/orchestration`. In the main Python job, Airflow tests skip through `importorskip`.

**Consequences.** DAG import errors, like the `conf` parameter that broke two DAGs, fail CI. Before this job they were invisible.

## ED-09: mlflow transformers flavor for model logging

**Context.** With mlflow 3, the pytorch flavor defaults to `torch.export`, and its fallback is pickle, which is the unsafe deserialization class behind several mlflow advisories. Without explicit requirements, mlflow also pinned torchvision.

**Decision.** Log with `mlflow.transformers.log_model`. The model is stored as safetensors, together with its tokenizer and the `text-classification` task, using `name=` and explicit `pip_requirements`. The new transformers 5 API is also applied:
- `eval_strategy` replaces `evaluation_strategy`;
- `warmup_steps` now takes the warmup ratio;
- `top_k=None` replaces `return_all_scores=True`.

**Consequences.** Registered models load without pickle. Models trained on the old stack load with identical scores, so no retraining is needed.

## ED-10: Raise coverage with tests, not by lowering the gate

**Context.** Coverage was 74% against an 80% gate.

**Decision.** Keep the gate and write behavioural tests for the least-covered modules that contain real logic. A `FakeSession` records SQL and parameters so tests can assert what is written. `train.py` stays at 0% rather than being excluded; it is verified with real training runs instead.

**Consequences.** Coverage is 85.8%, and the new tests caught assumptions that status-code tests would miss.

## ED-11: Slim, hardened runtime images

**Context.** Trivy flagged HIGH/CRITICAL findings, all inherited from the base images:
- setuptools' vendored packages in `python:3.11-slim`;
- the npm bundled with `node:20-alpine`, and Alpine system libraries.

The dashboard image also copied the host's `node_modules` and `.env`.

**Decision.**
- **Python images.** Uninstall setuptools and wheel after installing dependencies. They are build tools that are never imported at runtime. In the inference image this is a separate layer, so the torch layer stays cached.
- **All images.** Run as a non-root user and use Python-based healthchecks (slim images have no curl).
- **Dashboard.** `apk upgrade`, npm 11, `npm ci`, and a `.dockerignore`.

**Consequences.** All three images pass Trivy with the CI settings. A package that needs setuptools at runtime would have to reinstall it explicitly.

## ED-12: Keyed HMAC for DSAR identifiers

**Context.** DSAR searches stored a plain SHA-256 of the email or ID searched. Identifiers have low entropy, so anyone with database access could reverse them with a dictionary.

**Decision.** Store `HMAC-SHA256(DSAR_HASH_KEY, identifier)`. The key defaults to `JWT_SECRET_KEY`. The identifier itself never appears in any SQL statement or parameter, and a test checks this.

**Consequences.** Rotating the key breaks matching against older search records. That is acceptable for a search history, but do it deliberately.
