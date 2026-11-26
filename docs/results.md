# Results

This page shows what has been measured and verified so far, what is only a target, and what is still unmeasured. It reflects the state of the `audit/fixes` branch (November 2026).

**Summary**
- The code, test and supply-chain results below come from real runs.
- **No production model has been trained yet.** Model quality numbers are targets, not results.
- Latency and business KPIs have not been measured outside development.

---

## 1. Quality gates (CI)

Every check below was run locally with the same settings as `.github/workflows/ci.yml`.

| Check | Result | Gate |
|---|---|---|
| ruff 0.4.4 + black 24.4.2 | Clean | Blocking |
| Python tests (API, scanner, ML, ETL, security) | **317 passed, 4 skipped, 1 xfailed** | Blocking |
| Python line coverage | **85.8%**, up from 74% | `--cov-fail-under=80` |
| Airflow DAG import check + orchestration tests (in `apache/airflow:2.8.1-python3.11`) | **4 DAGs, no import errors, 47 passed** | Blocking |
| TypeScript: `tsc`, ESLint, Prettier | Clean | Blocking |
| Dashboard tests (Vitest 4) | Passing | Blocking |
| Integration and E2E (testcontainers) | Not run in this pass | Integration on PR, E2E nightly |

Notes:
- The 4 skipped tests depend on optional components that the plain Python job does not install. The Airflow tests run in their own job, and integration/E2E run separately.
- The single xfail is `test_full_name_idempotent`, a documented trade-off (see [engineering-decisions.md](engineering-decisions.md#ed-07-full_name-pseudonymisation-is-not-value-idempotent)). It is `strict=True`: if it ever starts passing, the suite fails so the trade-off is reviewed.
- `ml/training/train.py` is left at 0% coverage on purpose instead of being excluded from measurement. It was verified with a real end-to-end run instead (section 3).

### Coverage by area after the audit

The new tests target modules with real logic, and each of these is now at 100%: `api/routers/compliance.py`, `dsar.py`, `demo.py`, the lineage and remediation routes in `risks.py`, `api/services/export.py`, `ml/inference/model_loader.py` and `scanner/repository.py`.

The tests check behavior, not just status codes. A `FakeSession` records every SQL statement and parameter, so tests can assert, for example:
- that the DSAR identifier never appears in any statement;
- that audit payloads survive quotes and backslashes;
- that the status filter is actually sent.

The tarball path-traversal test was mutation-checked: it fails if extraction drops `filter="data"`.

---

## 2. Supply chain

| Scan | Before | After |
|---|---|---|
| pip-audit, all requirement files | **205 advisories in 13 packages** (and the audit could not resolve the dependencies) | **0** in `requirements.txt`, `api/requirements.txt`, `requirements-ml.txt`, `requirements-train.txt` (one ignore: `PYSEC-2022-42974`) |
| npm audit (high/critical) | 20 vulnerabilities | **0 high/critical**; 6 moderate remain in the ESLint 8 toolchain (dev only) |
| Trivy, API image (CRITICAL+HIGH) | 2 HIGH (setuptools vendored `jaraco.context`, `wheel`) | **0** |
| Trivy, inference image (CRITICAL) | Same setuptools findings | **0**, also clean at HIGH |
| Trivy, dashboard image (CRITICAL+HIGH) | 1 CRITICAL (`tar` in the bundled npm) + HIGHs (npm deps, Alpine openssl/musl/zlib) | **0** |

Main upgrades:
- fastapi 0.111 → 0.165, starlette → 1.10;
- python-jose → PyJWT 2.15;
- torch 2.3.0 → 2.15.0, transformers 4.41 → 5.24, mlflow 2.13 → 3.18;
- pyarrow → 23.0.1, pyspark → 3.5.9;
- Vite 7, Vitest 4.

A model saved with the old stack (torch 2.3 / transformers 4.41) loads on the new one and gives **identical scores (max difference 0)**, so deployed models do not need retraining.

---

## 3. Model

### What has been verified

- **The training pipeline runs end to end on the upgraded stack:**
  1. synthetic data generation;
  2. `train.py`;
  3. `evaluate.py`;
  4. mlflow registration;
  5. reloading from the registry;
  6. serving through `ml.inference`.
- **The inference image works:** it builds, loads the model at startup, passes its healthcheck, answers `/infer` and runs as non-root.

The verification run used a deliberately tiny dataset and a single short epoch to exercise the code paths. Its metrics (macro F1 ≈ 0.02) **say nothing about model quality** and must not be quoted as results.

### Targets (not yet measured on a full training run)

| Metric | Target | Where it is enforced |
|---|---|---|
| Macro F1, 10 classes | ≥ 0.90 | `ml/training/train.py` warns below `_MIN_F1_TARGET` |
| Per-class recall at confidence 0.85 | Reported per class | `ml/training/evaluate.py` threshold report |
| Coverage at threshold (share of columns scored ≥ 0.85) | Reported | `evaluate.py` |

To produce real numbers, run the training workflow (`.github/workflows/train.yml`) or run it locally:

```bash
pip install -r requirements-train.txt --extra-index-url https://download.pytorch.org/whl/cpu
python -m ml.data.synthetic_generator --out-dir ml/data/labeled --samples 10000
python -m ml.training.train --data-dir ml/data/labeled
python -m ml.training.evaluate --model-dir ml/models/pii-classifier \
    --test-data ml/data/labeled/test.jsonl --report-dir ml/reports --threshold 0.85
```

Then record the macro F1, the per-class recall and the threshold coverage in this section, with the model version.

**Known caveat.** The model is trained only on synthetic Faker data. Expect lower precision on real column names and values (internal abbreviations, free-text fields, mixed-language data). Measure on a labelled sample of real columns before the first production deployment.

---

## 4. Functional verification

Beyond the unit tests, these were run against real services:

| Scenario | Environment | Result |
|---|---|---|
| Smoke test of every API endpoint | API against PostgreSQL 16 | Pass |
| JWT edge cases: expired, wrong key, `alg=none`, malformed | Same | All return 401 |
| Remediation failure semantics (`dag.test()`) | Airflow 2.8.1 | Quarantine runs when anonymization fails; promote is skipped; notify and audit always run and record `remediation_failed` |
| Each image after hardening | Docker | API serves; inference answers `/infer`; dashboard dev server serves with `src/` mounted |

---

## 5. Defects fixed by the audit

The audit found and fixed these. Each fix has a regression test where it could be unit-tested.

| Area | Defect | Impact before the fix |
|---|---|---|
| Pipeline | `sampling_pipeline` and `remediation` failed to import on Airflow 2.8 (`conf` is a reserved context key) | **No table was ever sampled or remediated** |
| Pipeline | Remediation was triggered without the S3 path, format or flagged columns | Remediation only notified; nothing was anonymized or quarantined |
| Pipeline | Anonymize and quarantine ran in parallel on the same path; the anonymizer overwrote its own input | Data loss or un-anonymized data promoted |
| Pipeline | `:param::jsonb` is not a SQLAlchemy bind parameter | Quarantine and anonymizer metadata writes failed |
| Pipeline | Quarantine matched sibling prefixes (`data/users` vs `data/users_backup`) | Unrelated data quarantined |
| Pipeline | `quarantine_expiry` used columns that did not exist | **Retention never ran**: no warnings, no deletion |
| Pipeline | Classifier retries inserted duplicate findings | Inflated counts and scores |
| API | `/risks` status filter was ignored | Dashboard filters showed wrong data |
| API | Audit, ROPA, DSAR and lineage JSON built with f-strings | Quotes in notes broke inserts or injected keys |
| API | CORS did not allow PATCH | ROPA annotation failed from the browser |
| API | Risk exposure counted rows once per flagged column | Fine estimate inflated by the column count |
| API | DSAR stored a plain SHA-256 of the identifier | Reversible by dictionary attack on emails and IDs |
| API | CSV exports did not neutralize formulas | Spreadsheet formula injection |
| Scanner | Malformed payloads raising `TypeError` crash-looped consumers | Topic stalled |
| ETL | Phone masking treated `+180` as the country code for `+1-800-…` | Wrong output; not idempotent |
| ML | Inference endpoint was async but CPU-bound | Event loop blocked under load |
| ML | Tarball extraction without a filter | Path traversal from a malicious model archive |
| Dashboard | Data loads ran inside `useState` initializers | ROPA, DSAR and Intelligence tabs never refreshed; one spun forever on error |
| Dashboard | Clicking a data source did not filter; expired sessions were not logged out | Broken navigation, confusing 401s |
| CI | `ci.yml` was invalid YAML | **CI never ran** |
| CI/Infra | Dependencies unresolvable; inference Dockerfile could not find torch; healthchecks used `curl` (not in slim images) | Images and installs failed |

---

## 6. Not yet measured

| Item | Target | How to measure |
|---|---|---|
| API p95 latency | < 500 ms | `locust -f tests/perf/locustfile.py` (fails the run above target) |
| Inference p95 latency | < 2 s | Same locustfile |
| Detection coverage | ≥ 99% of new assets inspected within 24 h | `scanner_events` vs `pii_findings` timestamps |
| Compliance score | ≥ 90% of flagged tables remediated | Dashboard KPI card / `/stats/summary` |
| Median time to remediation | ≤ 5 business days | `/compliance/trends` (`avg_ttr_days`) |
| False positive rate | ≤ 10% | Findings marked as false positive by DPOs |
| Patrol DAG success rate | ≥ 99.5% | Airflow run history / Grafana |

These come from the KPI framework in [business-decisions.md](business-decisions.md#6-success-metrics). Fill in this table after the first month in a production-like environment.
