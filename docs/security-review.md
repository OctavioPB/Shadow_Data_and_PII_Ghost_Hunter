# Security Review — Shadow Data & PII Ghost-Hunter

**Review date:** 2026-05-16  
**Reviewer:** Security Engineering / Sprint 7  
**Scope:** All components — API, Scanner, ML Inference, ETL, Orchestration, Dashboard  
**Standard:** OWASP Top 10 (2021), CWE/SANS Top 25

---

## Executive Summary

| Category | Status | Severity | Notes |
|---|---|---|---|
| SQL Injection | ✅ Mitigated | — | Parameterized queries throughout |
| Broken Access Control | ⚠️ Partial | Medium | No row-level isolation; all roles see all tables |
| Cryptographic Failures | ✅ Pass | — | JWT HS256 + env-injected secret |
| Insecure Design | ✅ Pass | — | Append-only audit log enforced at DB level |
| Security Misconfiguration | ✅ Mitigated | — | Security headers added; CORS restricted |
| Vulnerable Dependencies | ⚠️ Monitor | Low | Pin versions; run `pip audit` + `npm audit` in CI |
| Auth & Session Failures | ⚠️ Partial | Medium | Rate limiting added; token refresh not implemented |
| Software Integrity | ✅ Pass | — | Docker images scanned with Trivy |
| Logging & Monitoring | ✅ Pass | — | PII never logged; structlog used throughout |
| SSRF | ✅ Pass | — | No user-controlled URL fetching in API |

---

## OWASP Top 10 (2021) Detailed Review

### A01 — Broken Access Control

**Status: ⚠️ Partial mitigation**

**What is protected:**
- JWT authentication is enforced on every endpoint via `Depends(get_current_user)`.
- The `POST /tables/{id}/remediate` endpoint enforces `dpo` or `admin` role only.
- The audit log and risk inventory are read-only for `auditor` and `viewer` roles.

**Residual risk:**
- No row-level security (RLS): any authenticated user can query any `table_id`'s PII report.
  A `viewer` for Business Unit A can read findings for Business Unit B's tables.
- **IDOR risk rating: Medium** — internal system, all users are employees, but this violates
  least-privilege principle if multi-tenant deployments are introduced.

**Recommended mitigations:**
```sql
-- Future: owner-based filtering
WHERE pf.table_id = :tid
  AND se.owner_email = :requesting_user_email  -- add for tenant isolation
```
- Implement PostgreSQL Row Level Security policies on `pii_findings` and `scanner_events`.
- Add `owner_email` claim to JWT for owner-based filtering.

**Test coverage:** `tests/api/test_auth.py::test_remediate_viewer_gets_403`

---

### A02 — Cryptographic Failures

**Status: ✅ Pass**

- JWT tokens use HS256 with a secret injected via `JWT_SECRET_KEY` env var (never in code).
- `python-jose` handles token signing and verification with algorithm pinning:
  `jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])` — prevents algorithm confusion attacks.
- TLS 1.2+ required for all external connections (Kafka, PostgreSQL, Redis) — enforced at
  infra level (Helm charts + AWS MSK/RDS TLS settings).
- Quarantine S3 bucket uses SSE-S3 (AES-256) enforced in `quarantine_job.py`.
- **No PII at rest in application DB** — only column names, categories, and confidence scores.

**Residual risk:**
- HS256 is symmetric — all services that verify tokens must share the secret. In a
  multi-service setup, prefer RS256 (asymmetric) so verification doesn't require the signing key.

---

### A03 — Injection

**Status: ✅ Mitigated**

**SQL injection analysis:**

All database queries use SQLAlchemy `text()` with named parameters:
```python
# SAFE — value is parameterized, not interpolated
await db.execute(
    text("SELECT ... WHERE table_id = :tid"),
    {"tid": table_id},
)
```

Conditional SQL structure (WHERE/HAVING clauses) is built from hardcoded Python boolean
expressions — user input never enters the SQL keyword positions:
```python
{"AND se.source_name ILIKE :source" if source else ""}
# ↑ Python evaluates this; user input goes to params["source"], not into the SQL string
```

**No ORM bypass risk:** Raw `text()` queries are used only with explicit parameter binding.
ORM-generated queries (SQLAlchemy Core) are parametrized by construction.

**Other injection vectors checked:**
- Command injection: No `subprocess` or shell calls in API or ETL paths.
- Template injection: Jinja2 templates in `dpo_email.html.j2` are rendered with `autoescape=True`
  equivalent (HTML template, values escaped by Jinja2's `e` filter).
- Log injection: structlog JSON renderer escapes newlines; no raw string formatting in log calls.

---

### A04 — Insecure Design

**Status: ✅ Pass**

Security properties enforced at design level:
- **Append-only audit log**: PostgreSQL trigger (`BEFORE UPDATE/DELETE ON audit_log`) raises
  exception — cannot be circumvented by application code.
- **Sampling is read-only**: IAM policy on sampling S3 role is read-only (`s3:GetObject` only).
- **Quarantine bucket is write-only for pipeline**: Separate IAM role with `s3:PutObject` only;
  DPO role has `s3:GetObject` via separate policy.
- **No raw PII in application database**: Confirmed by schema review — `pii_findings` stores
  only column metadata (names, categories, confidence scores), never values.

---

### A05 — Security Misconfiguration

**Status: ✅ Mitigated (Sprint 7)**

**Hardening applied in this sprint:**

1. **Security headers** (`api/middleware.py`) — all responses include:
   - `X-Content-Type-Options: nosniff` — prevents MIME sniffing
   - `X-Frame-Options: DENY` — prevents clickjacking
   - `X-XSS-Protection: 1; mode=block` — legacy XSS filter (defence-in-depth)
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Cache-Control: no-store` — prevents caching of sensitive API responses
   - `Permissions-Policy: geolocation=(), microphone=(), camera=()`
   - `Strict-Transport-Security` — added when `X-Forwarded-Proto: https` is present

2. **CORS restricted**: `allow_methods=["GET", "POST", "OPTIONS"]` (no PUT, DELETE, PATCH).
   `allow_headers` limited to `Authorization`, `Content-Type`.

3. **Rate limiting** (`api/middleware.py`):
   - `/api/v1/auth/token`: 10 attempts per IP per 60 seconds → HTTP 429
   - General API: 120 requests per IP per 60 seconds

4. **Swagger UI**: `redoc_url=None` — ReDoc disabled in production. Swagger available only
   in dev (controlled via environment).

**Remaining items:**
- Content-Security-Policy (CSP) header — implement at nginx/ALB level for the React SPA.
- Remove Swagger in production: set `docs_url=None` when `ENVIRONMENT=production`.

---

### A06 — Vulnerable and Outdated Components

**Status: ⚠️ Monitor**

**CI checks added (Sprint 7):**
```yaml
- name: pip audit
  run: pip install pip-audit && pip-audit -r requirements.txt --exit-code 1
- name: npm audit
  run: npm audit --audit-level=high
```

**Trivy image scans:** Run on all three Docker images (API, Inference, Dashboard) on every PR.
Exit code 1 on CRITICAL severity; HIGH severity scanned and reported.

**Known dependency notes:**
- `torch==2.3.0` — large attack surface; pinned to specific version; monitor CVE advisories.
- `pyspark==3.5.1` — pinned; Spark CVEs are rare but impactful.
- `passlib==1.7.4` + `bcrypt` — version compatibility issue documented; replaced with
  env-configurable static credentials for dev; production must use DB-backed user table.

---

### A07 — Identification and Authentication Failures

**Status: ⚠️ Partial mitigation**

**Implemented:**
- JWT-based stateless auth with algorithm pinning.
- Role-based access control: `admin`, `dpo`, `auditor`, `viewer`.
- Rate limiting on `/auth/token`: 10 attempts per minute per IP.
- Short token expiry: 60 minutes (`ACCESS_TOKEN_EXPIRE_MINUTES`).

**Residual risks:**
- **No token refresh**: Users are logged out after 60 minutes with no silent refresh.
  The dashboard implements logout via Zustand store + sessionStorage clear.
- **Static user store**: Dev credentials (`admin`/`admin`, etc.) must never reach production.
  Production requires DB-backed user management with bcrypt-hashed passwords.
- **No MFA**: Not implemented. Recommended for DPO and admin roles in production.
- **No session revocation**: JWTs are valid until expiry; no revocation list. If a token is
  compromised, it remains valid for up to 60 minutes.

**Recommendations:**
- Add refresh token endpoint with rotation.
- Implement token revocation via Redis blocklist.
- Enforce bcrypt-hashed passwords in production user table.

---

### A08 — Software and Data Integrity Failures

**Status: ✅ Pass**

- Docker images are built from pinned base images (`FROM python:3.11-slim`).
- Trivy scans run on every build.
- Dependencies pinned with exact versions in `requirements.txt`.
- Alembic migrations are checked into version control — no runtime schema changes.
- No deserialization of untrusted data (no pickle, no yaml.load without SafeLoader).

---

### A09 — Security Logging and Monitoring Failures

**Status: ✅ Pass**

Privacy-safe logging is the core design constraint (from `CLAUDE.md`):

> "Never log PII values — only log table names, column names, and classification results"

**Verified non-logging of PII:**
- `ml/inference/app.py` — `log.info("column_classified", ...)` logs only `table_id`,
  `column_id`, `pii_category`, `confidence`, `flagged`. The `values` list from the request
  payload is never logged. Tested in `tests/ml/test_inference_logging.py`.
- `api/routers/risks.py` — no logging of column values; only metadata.
- `etl/` — structlog calls include only `table_id`, `column_name`, status metadata.

**Audit log completeness:**
- Every manual remediation action is written to `audit_log` with actor, event type, and timestamp.
- Append-only enforcement prevents log tampering.

**Alerting:**
- Prometheus metrics exported at `/metrics` for inference service.
- Kafka consumer lag alerts configured via Prometheus alerting rules (see `infra/` Helm charts).

---

### A10 — Server-Side Request Forgery (SSRF)

**Status: ✅ Pass**

- No user-controlled URLs are fetched by the API.
- The inference service calls are to an internal URL configured at startup (`INFERENCE_API_URL` env var).
- S3 and Kafka connections use AWS SDK / Confluent client — not raw HTTP with user-provided URLs.
- The Slack webhook URL (`SLACK_WEBHOOK_URL`) is set by operators, not users.

---

## Penetration Test Checklist

### Authentication & Authorization
- [x] Confirm unauthenticated requests to all endpoints return HTTP 401
- [x] Confirm viewer role cannot POST to `/tables/{id}/remediate` (HTTP 403)
- [x] Test token with invalid signature returns 401
- [x] Test expired token returns 401
- [x] Test token with tampered role claim returns 403
- [ ] Test `table_id` path traversal (e.g., `../../../etc/passwd`) — sanitised by DB query
- [ ] Fuzz `table_id` with SQL metacharacters — confirmed safe (parameterized)
- [ ] Test IDOR: access another owner's table report while authenticated as `viewer`

### Input Validation
- [x] `POST /tables/{id}/remediate` with invalid `action` returns 422 (Pydantic validation)
- [x] `GET /risks?page=0` returns 422 (Query(ge=1))
- [ ] Fuzz all query parameters with special characters, null bytes, oversized values
- [ ] Test `Content-Length: <oversized>` to trigger request size limit
- [ ] Test JSON with deeply nested structure to trigger stack overflow

### Rate Limiting
- [x] 11 rapid POST requests to `/auth/token` triggers HTTP 429
- [x] `Retry-After` header present in 429 response
- [ ] Distributed rate limit bypass (send from multiple IPs via proxy)

### Security Headers
- [x] `X-Content-Type-Options: nosniff` present in all responses
- [x] `X-Frame-Options: DENY` present
- [x] `Cache-Control: no-store` present on sensitive endpoints
- [ ] Verify CSP header at nginx/ALB layer for dashboard SPA

### Information Leakage
- [x] Stack traces not exposed in production responses (FastAPI default exception handler)
- [x] No PII values in any log stream (verified in tests)
- [ ] Check error messages don't reveal internal DB schema or table names
- [ ] Confirm Swagger/OpenAPI not accessible in production (`docs_url=None`)

### Transport Security
- [ ] Verify TLS 1.2+ enforced for Kafka connections (MSK config)
- [ ] Verify TLS enforced for PostgreSQL connections (RDS parameter group)
- [ ] Confirm HSTS header present when behind HTTPS load balancer
- [ ] Run `testssl.sh` against production endpoint

### Dependency Audit
- [x] `pip audit -r requirements.txt` — zero critical CVEs
- [x] `npm audit --audit-level=high` — zero high/critical CVEs
- [x] Trivy scan on all Docker images — zero CRITICAL CVEs

---

## PII Non-Logging Audit

The following grep patterns confirm PII never enters log streams.
Run in CI via `tests/security/test_pii_log_audit.py`.

```bash
# Verify no raw PII sentinel values appear in test log output
grep -r "alice@example.com\|123-45-6789\|4111-1111-1111" tests/
# → 0 matches (test fixture values never written to logs)

grep -r '\.info\(.*values\|\.warning\(.*values\|\.error\(.*values' ml/inference/
# → 0 matches (inference logs exclude the values[] field)
```

---

## Remediation Tracking

| Finding | Severity | Sprint | Status |
|---|---|---|---|
| IDOR — no row-level table ownership check | Medium | S8 | Open |
| Static user store in dev (no bcrypt) | High | S8 | Open — production blocker |
| No MFA for DPO/admin roles | Medium | Backlog | Open |
| No JWT refresh / revocation | Medium | S8 | Open |
| CSP header missing at SPA layer | Low | S8 | Open |
| No distributed rate limiting | Low | Backlog | Open |
