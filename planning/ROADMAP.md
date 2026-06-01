# Product Roadmap — Shadow Data & PII Ghost-Hunter

> **How to read this document.** Each feature is described from the business outcome backward to
> the implementation. Business value is stated first, in measurable terms. Existing system assets
> that each feature leverages are called out explicitly — every feature here is an *extension* of
> what is already built, not a parallel track. Engineering tasks follow the business case; they
> exist to serve it, not the other way around.
>
> Features are ordered by expected business value, not by implementation ease.

---

## Feature Index

| # | Feature | Primary Beneficiary | Target Quarter | Effort |
|---|---|---|---|---|
| F-01 | [DSAR Accelerator — Individual-Level Data Footprint Search](#f-01-dsar-accelerator--individual-level-data-footprint-search) | Compliance Analyst / Legal | Q3 2026 | 3 sprints |
| F-02 | [Living ROPA Generator — Article 30 Always Current](#f-02-living-ropa-generator--article-30-always-current) | DPO / Legal | Q3 2026 | 2 sprints |
| F-03 | [DPO Approval Bot — One-Click Remediation from Slack](#f-03-dpo-approval-bot--one-click-remediation-from-slack) | DPO | Q4 2026 | 2 sprints |
| F-04 | [Compliance Intelligence Dashboard — Trend Analytics and Executive Reporting](#f-04-compliance-intelligence-dashboard--trend-analytics-and-executive-reporting) | DPO / Board / CFO | Q4 2026 | 2 sprints |
| F-05 | [Full-Catalog Retroactive Discovery — Close the Pre-Deployment Backlog](#f-05-full-catalog-retroactive-discovery--close-the-pre-deployment-backlog) | Platform Engineering / DPO | Q1 2027 | 3 sprints |
| F-06 | [Data Lineage Tracker — Cascade Erasure and Breach Impact Mapping](#f-06-data-lineage-tracker--cascade-erasure-and-breach-impact-mapping) | DPO / Legal / CISO | Q1 2027 | 4 sprints |

---

## F-01: DSAR Accelerator — Individual-Level Data Footprint Search

### The Business Problem

A Data Subject Access Request (DSAR) under GDPR Article 15 requires the organization to provide the individual with a copy of all personal data held about them within 30 days. The operational reality, before this feature, is:

1. A compliance analyst receives the DSAR.
2. They manually query every registered data source — production CRM, marketing platform, ERP, data warehouse — for records matching the individual's email or national ID.
3. They do not query shadow tables, because those are unknown.
4. The response is delivered. It is structurally incomplete.

The EU DPAs' 2024 enforcement data shows that **incomplete SAR responses are the second most common violation type** across all GDPR findings (after unlawful processing). The financial exposure from an incomplete SAR response is not just the direct fine — it is the DPA investigation that follows, which often uncovers broader governance failures and triggers a second, larger enforcement action.

More concretely: a compliance analyst at a mid-size organization spends **20–40 hours per DSAR** on manual data discovery. Organizations receiving 10 or more DSARs per year are spending 200–400 engineer-hours annually on this task — work that is both expensive and unreliable.

### The Value This Feature Delivers

- Reduces DSAR response preparation from 20–40 hours to under 2 hours.
- Makes shadow table contents searchable for the first time — the compliance analyst can confidently state "we searched all detected PII-containing data assets, including unregistered copies."
- Converts an incomplete, manual process into a documented, auditable search record.
- Every search is logged in the audit trail, which itself is evidence of DSAR compliance diligence.

**Estimated annual value:** At 12 DSARs/year × 30 hours saved × €650/day blended analyst cost, labor savings alone are approximately **€14,000/year**. The regulatory risk avoided from incomplete responses is higher but harder to quantify per incident.

### What This Feature Does (User Story)

> As a compliance analyst, when I receive a DSAR, I open the "Data Subject Search" panel, enter the individual's email address or national ID, and within 60 seconds receive a list of all detected data assets — including shadow tables — that contain records matching that identifier. I can export the search result to attach to the DSAR response file, and the search itself is automatically logged in the audit trail.

### Existing Assets Leveraged

| Asset | How it is used |
|---|---|
| `pii_findings` table | Contains the PII category and column name for every detected table — tells the search which columns in which tables to check |
| `column_samples` table | Contains the S3 path of the sampled Parquet file for each column — enables on-demand re-read of a column sample to check for the target identifier |
| `scanner_events` table | Provides source name, bucket, owner email, and row count context for each match |
| Inference service FastAPI (`ml/inference/app.py`) | The re-sampling architecture (read S3 → check column) follows the same pattern as the existing classification pipeline |
| `audit_log` table | Every search is recorded as an append-only entry with the search timestamp and initiating user (not the search term — never log PII values) |
| `api/routers/risks.py` | The new DSAR endpoint follows the same auth (`require_role("dpo", "admin")`) and DB session pattern |
| `api/auth.py` | JWT + RBAC restricts this endpoint to DPO and admin roles only — no viewer or auditor role can run a DSAR search |

### Implementation Tasks

**Task 1 — Database: Add `dsar_searches` table (Alembic migration)**
Create an append-only table to record every DSAR search: `search_id`, `initiated_by` (user email from JWT), `search_type` (email | national_id | phone), `search_timestamp`, `tables_searched_count`, `tables_matched_count`. Store `search_hash` (SHA-256 of the identifier being searched) instead of the raw value — preserves auditability without retaining PII in the search log.

**Task 2 — API: Implement `POST /api/v1/dsar/search` endpoint**
Accept `{ "identifier_type": "email" | "national_id" | "phone", "identifier_value": "..." }` from DPO/admin roles. For each table in `pii_findings` where `pii_category` matches the identifier type (EMAIL → columns where pii_category = 'EMAIL', etc.) and `status != 'remediated'`, read the S3 column sample (from `column_samples.sample_s3_path`), search for the identifier value, and return matched table IDs. The identifier value must never be written to any log or database field — it must exist only in memory for the duration of the request. Implement a 60-second request timeout and return partial results if timeout is reached (with a flag indicating the search was not exhaustive).

**Task 3 — API: Implement `GET /api/v1/dsar/search/{search_id}/export`**
Return a CSV file containing: `source_name`, `table_id`, `pii_categories`, `owner_email`, `estimated_row_count`, `last_scanned`, `current_status` for all tables matched in a given search. The CSV is the artifact the compliance analyst attaches to the DSAR response file.

**Task 4 — Audit log: Record search events without logging PII**
Every DSAR search must produce an `audit_log` entry: `event_type = 'dsar_search_initiated'`, `actor` = initiating user, `details_json = { "search_id": "...", "identifier_type": "email", "tables_searched": 42, "tables_matched": 3 }`. The raw identifier value must not appear in `details_json`. This is the evidence that the organization conducts systematic DSAR searches including shadow tables.

**Task 5 — Dashboard: Add "Data Subject Search" panel to the Info page or as a new nav item**
A form with two fields: Identifier Type (dropdown: Email / National ID / Phone) and Identifier Value (text input, masked on entry). A "Search Data Lake" button triggers the API call. Results display as a table: Source, Table ID, PII Categories Found, Owner, Status, Row Count. An "Export for DSAR Response" button at the bottom downloads the CSV. The search clears from the UI on navigation away — the identifier value is never persisted in the browser.

**Task 6 — Rate limiting and access control hardening**
The DSAR search endpoint is the only endpoint in the system that accepts PII values as input. Add: (a) a stricter rate limit (3 searches per hour per user, not 10 per minute), (b) IP allowlisting for this endpoint via environment variable (`DSAR_ALLOWED_IPS`), (c) an audit log entry for every failed access attempt (wrong role, rate limit exceeded). Add a Trivy scan rule to CI that fails the build if a search result is found containing the identifier value in any log output.

**Task 7 — Integration test: Verify the identifier value never persists**
Write an E2E test that runs a DSAR search, then queries `audit_log`, `dsar_searches`, and application logs to confirm the raw identifier value does not appear in any storage location. This test is the automated guarantee of the privacy-by-design constraint.

### Success Metrics

| Metric | Target |
|---|---|
| DSAR preparation time | ≤ 2 hours (from current 20–40 hours) |
| Search latency p95 | ≤ 60 seconds for a data lake with ≤ 500 detected tables |
| DSAR search coverage | 100% of tables with status ≠ 'remediated' included in search scope |
| Audit log completeness | 100% of searches produce an audit_log entry |
| PII value leakage | 0 occurrences in any log, DB field, or API response |

### Risks

| Risk | Mitigation |
|---|---|
| The search itself is a GDPR-regulated processing activity | DPIA for the DSAR search feature required before launch; search restricted to DPO/admin roles; no persistent storage of search terms |
| S3 re-read at search time is slow for large samples | Use the existing 1,000-row column sample (already in S3 from the classification pipeline) — do not re-sample the full table |
| False negatives: a table classified as EMAIL contains a non-email-formatted ID | Document limitation: search scope is constrained to columns classified in the queried PII category |

---

## F-02: Living ROPA Generator — Article 30 Always Current

### The Business Problem

GDPR Article 30 requires controllers to maintain a written record of all processing activities (the ROPA — Records of Processing Activities). The enforcement reality: the EDPB's Annual Report 2023 notes that **incomplete or out-of-date ROPAs were cited in 38% of DPA inspections** as a contributing compliance failure.

At most organizations, the ROPA is a spreadsheet maintained by the compliance team. It is updated when someone remembers to update it. New data assets created by engineering teams are not added to the ROPA because the compliance team does not know they exist. The ROPA is typically 6–12 months out of date by the time a DPA inspection request arrives.

This system already captures — automatically and continuously — the exact information required for ROPA entries: what PII categories exist, in which data sources, owned by whom, at what confidence level, and what has been done about them. The ROPA generator feature converts this machine-generated inventory into a DPA-ready document with no additional data collection effort.

### The Value This Feature Delivers

- The ROPA is never more than 24 hours out of date (bounded by the patrol DAG's detection latency).
- DPO preparation time for a DPA inspection drops from **2–4 weeks of manual compilation** to a single export action.
- Regulatory risk from an incomplete ROPA is eliminated for detected data assets (with an honest disclaimer for non-Kafka-connected systems).
- The document format can be aligned to the specific DPA's preferred submission format (most EU DPAs publish ROPA templates).

**Estimated annual value:** A DPO spending 3 weeks per year on ROPA maintenance at a senior compliance day-rate of €800/day saves **€12,000/year** in labor. More significant: a ROPA finding in a DPA inspection can escalate to a Tier 1 fine (up to €10M). The expected annual value of avoiding a ROPA-related enforcement action at a 1% annual probability is **€100,000/year**.

### What This Feature Does (User Story)

> As a DPO, when a DPA inspection notice arrives, I open the "Compliance Documents" panel and click "Generate ROPA Export." Within 2 minutes I have a structured document — available as both CSV and a formatted PDF — containing every data asset detected by the system: the data categories present, the data source, the owner, the retention status, and the remediation history. The document includes a cover section acknowledging the system's scope (Kafka-connected data assets only) so the declaration is accurate.

### Existing Assets Leveraged

| Asset | How it is used |
|---|---|
| `pii_findings` table | Provides `pii_category` (Art. 30(1)(c): categories of personal data) for each detected table |
| `scanner_events` table | Provides `source_name`, `data_source_type`, `owner_email` (Art. 30(1)(a)/(d): controller contact, recipient categories) |
| `quarantine_manifest` table | Provides retention period evidence: quarantine creation date + 30-day policy = Art. 30(1)(f) retention limit |
| `audit_log` table | Provides Art. 30(1)(g) security measures evidence: every entry demonstrates that a documented security process was applied |
| `api/routers/audit.py` | The existing CSV export endpoint pattern (`export_audit_log`) is the template for the ROPA export |
| `api/auth.py` | ROPA export restricted to DPO and admin roles (same pattern as audit log) |

### Implementation Tasks

**Task 1 — Database: Add `ropa_entries` view (no new tables, view over existing data)**
Create a PostgreSQL view `ropa_entries` that joins `scanner_events`, `pii_findings`, `quarantine_manifest`, and `audit_log` to produce one row per detected data source with: `data_source_name`, `data_source_type`, `pii_categories` (array_agg), `owner_email`, `first_detected_at`, `last_scanned_at`, `current_status`, `retention_limit` (quarantine creation + 30 days if quarantined, else NULL), `security_measures_applied` (boolean: has audit_log entry for this table_id). This is a read-only materialization of existing data — no new ETL.

**Task 2 — API: Implement `GET /api/v1/compliance/ropa` (JSON) and `GET /api/v1/compliance/ropa/export` (CSV)**
The JSON endpoint returns the structured ROPA data for dashboard rendering. The export endpoint returns a CSV formatted to match the EDPB's standard ROPA template column headers (Controller Name, DPO Contact, Processing Activity, Purpose, Data Categories, Data Subjects, Recipients, Retention Period, Security Measures, Cross-Border Transfers). Populate all fields that can be derived from system data; leave purpose and cross-border transfer fields as empty strings with a "Requires human input" comment in the header row.

**Task 3 — API: Implement PDF generation via `GET /api/v1/compliance/ropa/pdf`**
Use `weasyprint` (Python HTML-to-PDF library, no external service dependency, data stays in perimeter) to render the ROPA as a formatted PDF with: cover page (organization name from env var `ORGANIZATION_NAME`, generation date, DPO name from env var `DPO_NAME`, scope disclaimer), table of contents, one section per data source, and a signature block. The PDF is generated in-memory and returned as a download — it is never written to S3 or stored in the database.

**Task 4 — Dashboard: Add "Compliance Documents" panel accessible from the Info page**
A card showing: ROPA entry count (how many distinct data sources are in the ROPA), last updated timestamp, completeness indicator (percentage of ROPA entries with a documented purpose — requires human input for purpose field). Two buttons: "Export CSV" and "Export PDF." A warning banner if the system has detected tables in the last 24 hours whose ROPA entries are pending owner-purpose confirmation.

**Task 5 — API: Implement `PATCH /api/v1/compliance/ropa/{source_id}` for human-input fields**
Allow DPO/admin users to add processing purpose, legal basis, and cross-border transfer status to a ROPA entry. Store these in a new `ropa_annotations` table (source_name, purpose, legal_basis, cross_border_transfer, annotated_by, annotated_at). The ROPA export merges system-generated fields with human-annotated fields. Annotations are append-only (new annotation replaces old in the view, but history is preserved in the table).

**Task 6 — Audit logging: Record every ROPA generation event**
Every ROPA export (CSV or PDF) produces an `audit_log` entry: `event_type = 'ropa_exported'`, actor, format (csv/pdf), entry_count, timestamp. This log entry is itself evidence: it demonstrates that the DPO regularly reviews and exports the ROPA, which is a behavioral indicator of active compliance program management.

### Success Metrics

| Metric | Target |
|---|---|
| ROPA currency | ≤ 24 hours behind current data lake state |
| DPO ROPA preparation time | ≤ 30 minutes (from current 2–4 weeks) |
| ROPA completeness (auto-populated fields) | ≥ 80% of Art. 30 required fields populated automatically |
| PDF generation time | ≤ 120 seconds for a ROPA with ≤ 500 entries |

### Risks

| Risk | Mitigation |
|---|---|
| ROPA entries for non-Kafka data sources are missing | Cover page disclaimer: "This ROPA covers Kafka-connected data assets only. Manual supplement required for non-connected systems." |
| Purpose field is empty for most entries | UX: "Incomplete ROPA entries" counter on the dashboard incentivizes DPO to annotate; email reminder if entries have been pending annotation for > 7 days |
| PDF library (weasyprint) adds a large dependency | Evaluated alternatives: reportlab (no HTML input), puppeteer (requires Node.js). weasyprint is Python-native and accepts HTML input, consistent with the existing Jinja templating in `dpo_notifier.py` |

---

## F-03: DPO Approval Bot — One-Click Remediation from Slack

### The Business Problem

The current remediation workflow has a structural friction point: when a PII finding arrives, the DPO must open a browser, navigate to the dashboard, find the specific finding, read the PII Report, and click an action. In practice, this workflow is interrupted constantly — the DPO is in a meeting, traveling, or working from a mobile device.

The measured result of this friction is visible in the **time-to-remediation metric**: organizations in the pilot report a median of 3–5 business days between DPO notification and remediation action. During those 3–5 days, the flagged data remains active and accessible — the organization continues to be in violation of Article 5(1)(e) (storage limitation) for every day the data persists without action.

The `dpo_notifier.py` module already sends Slack messages with Block Kit. The Slack message already contains a button — but that button links to the dashboard (a browser action). Upgrading that button to an in-Slack approval action eliminates the context-switch entirely.

### The Value This Feature Delivers

- Reduces median time-to-remediation from 3–5 business days to under 30 minutes.
- The DPO can action a finding from a mobile device in 10 seconds.
- Every Slack action is still routed through the API (`POST /api/v1/tables/{id}/remediate`) and logged in the `audit_log` — the compliance evidence chain is unbroken.
- Eliminates the scenario where a DPO is on vacation for a week and 5 findings accumulate without action.

**Estimated annual value:** If the system detects 50 tables/year requiring DPO action, and each day of delay represents continued exposure, compressing 3–5 days of response time to <30 minutes across 50 findings represents **150–250 fewer days of active compliance exposure per year**. In regulatory terms, this changes the evidence from "we found the problem and acted in 4 days" to "we found the problem and acted in 20 minutes" — a significant differentiator in DPA settlement negotiations.

### What This Feature Does (User Story)

> As a DPO, when PII is detected in a shadow table, I receive a Slack message on my phone. The message shows the table name, detected PII categories, confidence score, and data owner. Three buttons are presented: "Quarantine," "Anonymize Now," and "False Positive." I tap "Quarantine." The system confirms the action, the data is moved to the quarantine bucket, and the audit log is updated — all without opening a browser.

### Existing Assets Leveraged

| Asset | How it is used |
|---|---|
| `dpo_notifier.py` — `_render_slack_message()` | Already produces Block Kit JSON with an `actions` block. The "Review in Dashboard" button is replaced with three interactive buttons. The rendering logic is a minimal extension of existing code. |
| `etl/notifiers/templates/` | The Slack message context (`NotificationContext`) already contains all fields needed for the button labels: `table_id`, `flagged_categories`, `max_confidence`, `source_name` |
| `POST /api/v1/tables/{id}/remediate` | The existing remediation endpoint is the backend for every button press. The Slack bot sends the same payload the dashboard sends — no new API surface. |
| `api/auth.py` — JWT RBAC | The bot authenticates using a long-lived service account JWT scoped to the DPO role, stored in environment variable `SLACK_BOT_SERVICE_TOKEN`. No human credentials stored in Slack. |
| `audit_log` | Every Slack-initiated remediation produces the same `audit_log` entry as a dashboard-initiated one — `actor` field records the DPO's Slack user ID mapped to their system account. |
| `notifications` table | The existing notification delivery log tracks the Slack interactive message as a sent notification; the action acknowledgment is logged as a follow-on entry. |

### Implementation Tasks

**Task 1 — Slack App configuration: Create a Slack App with Interactive Components enabled**
Register a Slack App in the organization's workspace with: `chat:write` and `chat:write.public` OAuth scopes, Interactivity enabled (requires a public HTTPS endpoint — the existing FastAPI API server), and a slash command `/pii-status` for on-demand compliance score queries. Store `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET` as environment variables. Add both to `.env.example`.

**Task 2 — API: Add `POST /api/v1/slack/interactions` webhook endpoint**
This endpoint receives Slack's interaction payloads (button press events). It must: (a) verify the `X-Slack-Signature` HMAC header using `SLACK_SIGNING_SECRET` (prevents spoofed payloads), (b) extract the `action_id` (quarantine | anonymize | false_positive), `table_id` (from the button's `value` field), and `user.id` (Slack user ID), (c) map the Slack user ID to a system user account via a `slack_user_mapping` table, (d) call the existing remediation logic (same code path as `POST /api/v1/tables/{id}/remediate`), (e) respond to Slack within 3 seconds (Slack's timeout) with an acknowledgment. If the remediation takes longer than 3 seconds, acknowledge immediately and post a follow-up message.

**Task 3 — Database: Add `slack_user_mapping` table (Alembic migration)**
Maps Slack user IDs to system user accounts: `slack_user_id`, `system_email`, `system_role`, `created_at`. Only DPO-role Slack users should be in this table. The mapping is managed by the admin user via a simple API endpoint (`POST /api/v1/admin/slack-users`). If a Slack user ID is not in the mapping, the interaction is rejected with a Slack ephemeral message: "Your Slack account is not authorized to take compliance actions. Contact the platform admin."

**Task 4 — Notifier: Upgrade Slack Block Kit message to interactive buttons**
Modify `_render_slack_message()` in `dpo_notifier.py` to replace the single "Review in Dashboard" link button with three action buttons: "Quarantine" (`action_id: quarantine`, `style: primary`), "Anonymize Now" (`action_id: anonymize`, `style: danger`, with a confirmation dialog: "This action is irreversible. Confirm anonymization?"), and "False Positive" (`action_id: false_positive`). Each button's `value` field contains the `table_id`. Add a context block showing the data owner and the 30-day quarantine retention reminder.

**Task 5 — Notifier: Add action confirmation follow-up message**
After a button is pressed and the remediation completes, post a follow-up Slack message in the same thread: "Action taken: [Quarantine/Anonymize/False Positive] by @username at [timestamp]. Audit log entry ID: [uuid]." This gives the DPO a confirmation receipt in Slack without needing to open the dashboard. If the remediation fails (API error), post a failure message with a direct link to the dashboard finding.

**Task 6 — Slash command: `/pii-status`**
Implement a Slack slash command that returns the current Compliance Score and a count of open findings awaiting DPO action, as a private (ephemeral) message visible only to the requesting user. This gives the DPO an on-demand status check without opening the browser.

**Task 7 — Security test: Verify payload signature validation**
Add a security test that sends a forged Slack interaction payload (invalid `X-Slack-Signature`) and verifies the endpoint returns 403 without processing the action or writing to the audit log. This is the primary security control for this feature — without it, anyone who knows the endpoint URL can trigger remediations.

### Success Metrics

| Metric | Target |
|---|---|
| Median time-to-remediation | ≤ 30 minutes (from current 3–5 business days) |
| Slack action delivery success rate | ≥ 99% |
| Slack-initiated audit log completeness | 100% (every Slack action produces an audit log entry) |
| Unauthorized action attempts blocked | 100% (forged signatures rejected, unmapped users rejected) |

### Risks

| Risk | Mitigation |
|---|---|
| Accidental "Anonymize Now" on a table that shouldn't be anonymized | Confirmation dialog before irreversible actions; 5-minute undo window (quarantine first, then convert to anonymization) — see `dag_remediation.py` remediation path |
| Slack service outage during active DPA investigation | Dashboard remains the primary interface; Slack is an accelerator, not a dependency. All finding data remains accessible in the dashboard. |
| Service account JWT compromise | JWT scoped to DPO role only; short expiry (8 hours); auto-rotation via GitHub Actions scheduled workflow |

---

## F-04: Compliance Intelligence Dashboard — Trend Analytics and Executive Reporting

### The Business Problem

The current dashboard answers one question well: *what is the compliance posture right now?* It does not answer the questions a board, a CFO, or a cyber insurance underwriter actually asks:

- *Are we getting better or worse over time?*
- *At the current remediation rate, when will we reach 100% compliance?*
- *What is our estimated regulatory fine exposure today, expressed in euros?*
- *Can I have a one-page PDF to put in the board pack?*

These are not technical questions — they are risk management questions. The `audit_log` and `pii_findings` tables contain all the raw data to answer them. Without this feature, a DPO answering these questions manually spends 3–5 hours per quarter compiling a spreadsheet from exported CSVs. The resulting report is static, backward-looking, and prepared under time pressure.

### The Value This Feature Delivers

- Eliminates quarterly manual compliance reporting: the board-ready PDF is generated in one click.
- The projected compliance score trend enables proactive remediation scheduling — the DPO can see that at the current pace, the score will drop below 90% in 3 weeks and schedule a review before it happens.
- The estimated fine exposure calculation (based on open finding count × estimated record count × regulatory fine probability model) gives the CFO a number to put in the risk register — converting a qualitative risk into a quantifiable one.
- Cyber insurance underwriters increasingly require demonstrated compliance metrics as a condition of favorable premium pricing; this feature produces exactly that evidence.

**Estimated annual value:** A DPO saving 5 hours/quarter on compliance reporting = **€6,500/year** in labor. More significant: the estimated fine exposure number, properly maintained and presented to the board quarterly, converts an unmanaged risk into a managed one. Insurance actuaries who see this type of demonstrated control have historically applied 15–25% premium reductions for cyber insurance in the data governance risk category — at typical SME premiums of €50,000–€200,000/year, this represents **€7,500–€50,000/year** in insurance savings.

### What This Feature Does (User Story)

> As a DPO, I open the Compliance Intelligence view and see: (a) a 90-day rolling compliance score trend line, (b) the average time-to-remediation over the past 30 days vs. the prior 30 days (improving or worsening), (c) an estimated regulatory fine exposure card — calculated as open flagged records × median fine per record for our organization's revenue tier — with a confidence interval, (d) a projected compliance score 30 days from now based on the current remediation velocity. I click "Generate Board Report" and download a 3-page PDF ready for the next board meeting.

### Existing Assets Leveraged

| Asset | How it is used |
|---|---|
| `audit_log` table | Every remediation event has a timestamp — time-to-remediation is `remediation_timestamp - detection_timestamp` per table_id, derivable from the existing append-only log |
| `pii_findings` table | Historical finding counts per day are derivable from `created_at` timestamps; compliance score trend is `remediated / total_flagged` grouped by week |
| `scanner_events` table | `estimated_row_count` provides the record volume estimate for the fine exposure calculation |
| `GET /api/v1/stats/summary` | The existing stats endpoint is the seed for the trend API; the new endpoint extends it with time-series parameters |
| React dashboard (`dashboard/src/App.tsx`) | The existing KPI card components, `Eyebrow`, `Hero`, and `KpiCard` patterns are reused for the new view |
| `api/routers/audit.py` — `export_audit_log` | The PDF generation pattern (HTML → PDF via weasyprint, introduced in F-02) is reused for the board report |

### Implementation Tasks

**Task 1 — API: Add `GET /api/v1/compliance/trends` endpoint**
Returns a time-series dataset: for each week in the trailing 90 days, return `{ week_start, total_detected, total_remediated, total_pending, compliance_score, avg_time_to_remediation_days, new_findings_count }`. All fields are derivable from `pii_findings` and `audit_log` with GROUP BY date_trunc('week', ...) queries. No new data collection required.

**Task 2 — API: Add `GET /api/v1/compliance/forecast` endpoint**
Implement a linear regression projection: take the trailing 4-week remediation velocity (remediations/week) and pending finding count to project the compliance score for the next 30 days. Return `{ projected_score_30d, projected_score_60d, remediation_velocity_per_week, days_to_100_pct | null }`. This is a 10-line calculation, not a separate ML model — purely arithmetic over existing aggregated data.

**Task 3 — API: Add `GET /api/v1/compliance/risk-exposure` endpoint**
Calculate estimated fine exposure: `sum(estimated_row_count) FILTER (WHERE status = 'flagged' AND status != 'remediated')` from `scanner_events`, multiplied by a configurable fine-per-record parameter (`FINE_PER_RECORD_EUR`, default: €0.004, derived from median GDPR fine per affected individual in published DPA decisions). Return `{ total_estimated_records, estimated_fine_low_eur, estimated_fine_high_eur, methodology_note }`. The `methodology_note` field is a static string explaining the calculation basis, intended for the board report footnote.

**Task 4 — Dashboard: Add "Compliance Intelligence" tab to the Info page (or as a dedicated nav item)**
New view with four cards: (1) 90-day compliance score trend line chart (using the existing CSS-in-JS inline chart pattern from `DataSourcesPage` heatmap, or a minimal SVG sparkline — no new charting library dependency), (2) Time-to-Remediation trend (this week vs. last month), (3) Estimated Fine Exposure range with methodology tooltip, (4) Projected Compliance Score in 30 days with a confidence range. All data from the three new API endpoints above.

**Task 5 — API + Dashboard: Implement "Generate Board Report" PDF**
A single-click action that calls a new `GET /api/v1/compliance/report/pdf` endpoint. The PDF contains: cover page (organization name, reporting period, DPO signature block), Compliance Score trend chart (rendered as SVG embedded in HTML → weasyprint), open findings summary table, estimated fine exposure, and a 3-sentence narrative paragraph generated from the metric values (template-based, not AI-generated: "As of [date], the compliance score is [N]%. [N] tables containing PII remain pending remediation. At the current remediation rate of [N] tables/week, full remediation is projected by [date].").

**Task 6 — Scheduled report: Weekly compliance digest email to DPO**
Add an Airflow DAG `dag_weekly_compliance_digest.py` (schedule: `@weekly`) that calls the `/compliance/trends` endpoint, renders a condensed HTML email via a new Jinja template, and sends it via the existing `dpo_notifier.py` email path. The email contains the current compliance score, the week-over-week change, and a count of new findings requiring DPO attention. No new infrastructure — reuses the existing email delivery and retry logic.

### Success Metrics

| Metric | Target |
|---|---|
| Board report generation time | ≤ 2 minutes |
| Trend data freshness | Updated within 1 hour of any remediation action |
| Forecast accuracy (30-day) | Within ± 10 percentage points of actual score (validated over first 90 days post-launch) |
| DPO quarterly reporting time | ≤ 30 minutes (from current 3–5 hours) |

### Risks

| Risk | Mitigation |
|---|---|
| Fine exposure calculation is presented as authoritative | Methodology note and confidence range on every display; explicit "for internal planning purposes only — not a legal opinion" disclaimer in the PDF |
| Trend data misleads if the data lake grows (more new findings than remediations) | Score trend shows both absolute finding count and compliance percentage — growth in the data estate is visible as a separate signal from compliance program effectiveness |

---

## F-05: Full-Catalog Retroactive Discovery — Close the Pre-Deployment Backlog

### The Business Problem

The current system detects PII in data assets *created after deployment*. It catches every new table via the Kafka `table.created` event and the daily patrol DAG. But organizations deploying this system for the first time are not starting from a clean slate.

A 5-year-old data platform has 5 years of accumulated shadow data. An engineering team that has been running quarterly analytics, ML training pipelines, and ETL staging environments since 2021 may have thousands of unregistered, unclassified tables sitting in S3 — none of which will ever produce a `table.created` event because they already exist.

This is the pre-deployment backlog, and it is typically the largest single body of compliance exposure an organization discovers when they implement a data governance program. The GDPR statute of limitations for fines is 5 years from the date of the violation — meaning that a table created in 2022 and discovered in a 2026 DPA inspection can still attract a fine.

**The business value of closing this backlog is not incremental — it is categorical.** An organization that deploys this system but does not run a retroactive scan can accurately say "we detect all new PII" but cannot say "we know what PII we currently hold." Only the latter statement satisfies the ROPA completeness requirement and provides a defensible answer to a DPA inspection question.

### The Value This Feature Delivers

- Converts the ROPA from a forward-looking register to a complete inventory of all current data assets.
- Eliminates the regulatory exposure from unknown historical shadow data.
- Provides a one-time audit report quantifying the scale of the backlog — directly informative for a board-level risk assessment.
- The ongoing periodic rescan (weekly, not just on new events) closes the coverage gap for non-Kafka data creation paths (CLI uploads, legacy batch jobs, manual exports).

**Estimated annual value:** Discovering and remediating historical shadow data is not quantifiable as a recurring saving — it is a one-time risk elimination event. For an organization with 500 unregistered tables containing PII, each representing an independent LGPD infraction at €9M maximum per infraction, the theoretical exposure reduction is in the hundreds of millions. The realistic expected value (at enforcement probability × actual table PII content) is still in the tens of thousands of euros per year in avoided expected fine.

### What This Feature Does (User Story)

> As a platform engineer, I run a one-time "Catalog Discovery Scan" from the dashboard, which kicks off an Airflow DAG that crawls the entire AWS Glue catalog and S3 bucket inventory, samples every table not already in `scanner_events`, and classifies it through the existing inference service. Within 48–72 hours I have a complete PII inventory of the data lake — not just new tables but everything. From that point forward, a weekly rescan ensures that any tables missed by the Kafka event path are caught within 7 days.

### Existing Assets Leveraged

| Asset | How it is used |
|---|---|
| `dag_sampling_pipeline.py` | The sampling and classification pipeline is unchanged — this feature provides a new *trigger* (Glue catalog crawl) that feeds the same existing pipeline |
| AWS Glue catalog (existing infrastructure) | `boto3 glue.get_tables()` enumerates all tables in registered Glue databases — this is the catalog crawl source |
| `scanner_events` table | Existing deduplication logic (`INSERT ... ON CONFLICT (event_id) DO UPDATE`) ensures tables already scanned are skipped automatically |
| `PIIClassifierOperator` | The existing Airflow operator that calls the inference service handles the classification — no changes needed |
| AWS S3 bucket list (existing IAM permissions) | `boto3 s3.list_objects_v2()` enumerates S3 objects not catalogued in Glue — catches the uncatalogued edge case |
| `api/routers/risks.py` | A new `POST /api/v1/admin/catalog-scan` endpoint triggers the Airflow DAG via the Airflow REST API — same pattern as existing DAG-triggering in `dag_patrol_new_tables.py` |

### Implementation Tasks

**Task 1 — New Airflow DAG: `dag_catalog_discovery.py`**
Create a new DAG with two run modes controlled by a `conf` parameter: `mode: full` (one-time complete catalog crawl, for the initial backlog scan) and `mode: incremental` (weekly, crawls only tables modified in the last 7 days). The DAG: (a) calls `boto3 glue.get_tables()` for all Glue databases registered in the organization's account, (b) calls `boto3 s3.list_objects_v2()` for each S3 prefix registered as a data source in `scanner_events.bucket`, (c) for each discovered table or S3 prefix, checks whether an entry exists in `scanner_events` with `status != 'pending'` — if it does, skip; if not, create a new `scanner_event` record and trigger the existing sampling pipeline. The DAG must be idempotent: running it twice does not re-classify already-classified tables.

**Task 2 — IAM: Verify existing read-only role covers Glue catalog enumeration**
The existing `SamplingRole` IAM policy covers `s3:GetObject` and `s3:ListBucket`. Add `glue:GetDatabases`, `glue:GetTables`, and `glue:GetTable` to the policy. Update the Terraform IAM policy resource in `infra/terraform/iam.tf`. Run `terraform plan` to confirm the change is additive (no destructive changes to existing permissions).

**Task 3 — API: Add `POST /api/v1/admin/catalog-scan` endpoint**
Admin-only endpoint that triggers the `dag_catalog_discovery` DAG via the Airflow REST API (`POST /dags/catalog_discovery/dagRuns`). Accepts `{ "mode": "full" | "incremental", "dry_run": true | false }`. In `dry_run` mode, returns the count of tables that would be scanned without actually triggering sampling. Returns `{ "dag_run_id": "...", "estimated_tables_to_scan": N, "estimated_completion_hours": N }`.

**Task 4 — Dashboard: Add "Catalog Scan" panel to the admin section of the Info page**
A card visible only to admin-role users. Shows: last catalog scan date, last scan scope (full/incremental), tables discovered by last scan, tables pending classification from last scan. A "Run Full Scan" button (with confirmation: "This scan will assess all unclassified tables in the data lake. Estimated duration: 24–72 hours depending on data lake size. Proceed?") and a "Schedule Weekly Scan" toggle that activates the weekly incremental mode.

**Task 5 — Monitoring: Add catalog scan progress metrics to Prometheus**
Emit a `catalog_scan_tables_discovered` counter and a `catalog_scan_tables_pending_classification` gauge from the DAG. Add a Grafana panel for catalog scan coverage: (total tables in Glue catalog) vs. (tables with a `scanner_events` record) — the gap is the uncovered backlog. This panel gives the DPO a live view of catalog coverage percentage.

**Task 6 — Backlog report: One-time "discovery audit" export**
After the first full catalog scan completes, generate a one-time "Discovery Audit Report" — an automated PDF (reusing the weasyprint infrastructure from F-02) listing: total tables scanned, tables with PII found (by category), tables clean, tables pending manual review. Include a histogram: PII findings by data source age (creation year). This report is the primary deliverable for the board's one-time risk quantification of the historical backlog.

### Success Metrics

| Metric | Target |
|---|---|
| Catalog coverage (after first full scan) | ≥ 95% of Glue-catalogued tables have a `scanner_events` record |
| Incremental scan catch rate (tables missed by Kafka) | ≥ 99% of non-Kafka-created tables detected within 7 days |
| Backlog scan duration | ≤ 72 hours for a data lake with ≤ 10,000 tables |
| Sampling idempotency (no re-classification of already-classified tables) | 0 duplicate `pii_findings` records for any `table_id` |

### Risks

| Risk | Mitigation |
|---|---|
| Catalog scan overwhelms the inference service with 10,000 simultaneous requests | DAG uses `max_active_tasks = 10` and a 60-second sleep between task groups; the inference service handles ≤ 50 column batches per request — the scan is throttled, not concurrent |
| Discovering thousands of historical findings creates a DPO alert backlog | The initial full-scan findings are batched into a single "Discovery Audit" digest notification rather than individual finding emails; individual emails resume for new findings after the initial scan |
| Glue catalog does not cover all S3 data (uncatalogued objects) | Task 1 includes an S3 prefix sweep as a complement to the Glue catalog crawl, covering objects outside registered Glue databases |

---

## F-06: Data Lineage Tracker — Cascade Erasure and Breach Impact Mapping

### The Business Problem

When a shadow table is detected and remediated, the compliance team closes the finding. But the shadow table may not be the end of the data lineage chain — it may itself have been the source for one or more downstream derived tables, ML training datasets, or analytical summaries. Remediating the source does not remediate the derivatives.

This gap creates three specific compliance failures:

**1. Erasure requests are incomplete.** Under GDPR Article 17, when an individual requests deletion of their data, the organization must erase all copies. If a shadow table was used as a source for a downstream table that is not detected (because it was created before the Kafka integration, or because it was created in a non-Kafka-connected environment), the erasure is incomplete. The organization has complied with the letter of the request for the detected table but not for its derivatives.

**2. Breach scope is underestimated.** During a breach investigation, the question is not just "which tables were accessible?" but "which tables were derived from those tables?" A shadow table that was used as the source for a model training dataset exposes not just the shadow table's records but also the model's memorized training examples, the validation set, any intermediate feature tables, and any downstream reports that contain aggregated statistics attributable to specific individuals.

**3. Root cause of shadow data proliferation is invisible.** Without lineage, the compliance team sees a stream of individual findings but cannot identify that 80% of all shadow tables in a given month trace back to a single upstream source — a particular production table being accessed without governance controls. Lineage turns individual findings into systemic risk patterns.

### The Value This Feature Delivers

- Cascade erasure: when a table is remediated, automatically identify and queue its known derivatives for review.
- Breach impact scoping: given a breached table, trace all known derivatives within seconds — critical for the 72-hour Article 33 notification window.
- Root cause identification: surface the upstream sources that are generating the most downstream shadow data, enabling targeted governance interventions (e.g., adding access controls to a specific production table that accounts for 40% of all shadow findings).

**Estimated annual value:** The value of this feature is concentrated in two scenarios: (1) a DSAR or erasure request that would otherwise result in an incomplete response, and (2) a breach whose scope would be underestimated without derivative visibility. The expected annual value is difficult to quantify precisely but is comparable to the breach notification scenario analyzed in BUSINESS.md Section 25 — incomplete breach notification is an independent enforcement risk on top of the underlying breach, typically adding 20–40% to the base fine.

### What This Feature Does (User Story)

> As a compliance analyst, when I remediate a shadow table, the system shows me a "Lineage Map" for that table — which upstream source it was likely derived from, and which downstream tables may have been derived from it. If I click "Cascade Review," all known derivative tables are added to the DPO's review queue automatically. When a breach is reported, I open the "Breach Impact" view, select the breached table, and within 30 seconds see a lineage graph showing all known derivatives — this populates the "categories and approximate number of records" fields in the Article 33 notification.

### Existing Assets Leveraged

| Asset | How it is used |
|---|---|
| Kafka events (`raw_event JSONB` in `scanner_events`) | The `raw_event` field already captures the full source event metadata — for Glue tables, this includes query history in some configurations; for S3 files, the event includes the copy source URI if the file was created by `s3:CopyObject` |
| `scanner_events.source_name` | S3 URI patterns often embed lineage signals: `staging/derived_from_customer_pii/2024-03-15/` — the path prefix encodes the upstream source. A regex-based heuristic over existing `source_name` values can bootstrap lineage without new data collection. |
| Airflow DAG metadata (Airflow DB) | Airflow's own metadata database records which DAGs wrote to which S3 paths. An Airflow API query (`GET /dags/{dag_id}/dagRuns/{run_id}/taskInstances`) returns task output metadata that can be cross-referenced against `scanner_events.source_name`. |
| `audit_log` table | Lineage events (derived-from relationships confirmed by the compliance analyst) are stored as `audit_log` entries: `event_type = 'lineage_confirmed'`, `details_json = { "parent_table_id": "...", "child_table_id": "..." }`. The append-only log is the lineage store. |
| `pii_findings` table | When two tables share the same PII category profile (same column names, same PII categories) and one was created after the other, probabilistic lineage inference flags them as likely parent-child pairs. |

### Implementation Tasks

**Task 1 — Database: Add `lineage_edges` table (Alembic migration)**
Create a table storing directed lineage relationships: `parent_table_id` (FK to scanner_events), `child_table_id` (FK to scanner_events), `confidence` (float: 0.0–1.0), `inference_method` (s3_copy_event | path_heuristic | airflow_metadata | analyst_confirmed), `created_at`. A `confidence` of 1.0 means the relationship was manually confirmed by a compliance analyst; lower values are inferred. This table is the lineage graph — it is purely additive to the existing schema.

**Task 2 — Lineage inference service: S3 copy event method**
When a `file.moved` or `table.created` Kafka event is processed by the scanner consumer, check whether the event's `raw_event` contains a `CopySource` field (set by `s3:CopyObject` operations). If it does, create a `lineage_edges` record with `parent_table_id` = the source table's scanner event ID (looked up by `source_name`), `child_table_id` = the current table's scanner event ID, `confidence = 1.0`, `inference_method = s3_copy_event`. This is the highest-confidence lineage signal and requires no new data collection — only a new processing step in the existing `file_moved_consumer.py`.

**Task 3 — Lineage inference service: Path heuristic method**
Add a daily DAG task that groups `scanner_events` by S3 path prefix depth. Tables with `source_name` that share a common prefix at depth 3 or greater (e.g., `s3://analytics/customer_data/...`) are flagged as likely lineage relatives. Score by temporal proximity (child created after parent within 7 days = higher confidence). Insert `lineage_edges` records with `confidence = 0.6`, `inference_method = path_heuristic`. These inferred edges are displayed to the compliance analyst for confirmation or rejection.

**Task 4 — Lineage inference service: Column profile similarity method**
For each new `pii_findings` entry, compute a "PII column fingerprint" — the sorted array of `column_name:pii_category` pairs for all flagged columns in the table. Search `pii_findings` for existing tables with a Jaccard similarity ≥ 0.8 to the new table's fingerprint and a `created_at` earlier than the new table. Insert `lineage_edges` records with `confidence = 0.7`, `inference_method = column_profile_similarity`. This catches derived tables that renamed some columns but retained the PII structure — a common pattern in analytics workflows.

**Task 5 — API: Add `GET /api/v1/tables/{id}/lineage` endpoint**
Returns the lineage graph for a table: `{ "parents": [...], "children": [...] }` where each entry contains `table_id`, `source_name`, `pii_categories`, `status`, `confidence`, `inference_method`. The compliance analyst uses this view before taking a remediation action — if the table has confirmed children, cascade review is prompted.

**Task 6 — API: Add `POST /api/v1/tables/{id}/lineage/cascade-review`**
DPO/admin action that adds all child tables (in `lineage_edges` where `parent_table_id = id` and `confidence >= 0.7`) to the active review queue. Each child table's `scanner_events.status` is updated from `classified` back to `flagged` (triggering a DPO notification), and an `audit_log` entry records the cascade review initiation.

**Task 7 — Dashboard: Add Lineage Map to the PII Report page**
Below the column findings table in the existing PII Report page, add a "Lineage" section. Render the parent-child graph as a simple SVG node-link diagram (no new library — pure SVG with CSS-in-JS, consistent with the project's no-external-CSS-framework constraint). Each node is clickable, navigating to the linked table's PII Report. A "Cascade Review" button triggers Task 6 if the analyst wants to queue all children.

**Task 8 — Breach impact view: Add `GET /api/v1/breach/impact/{table_id}`**
A DPO-only endpoint that returns the complete lineage subgraph rooted at the given table: all parents and all children, recursively, up to 5 levels of depth. Include for each node: `pii_categories`, `estimated_row_count`, `status`. Return a `total_estimated_records` sum across the subgraph — this is the number that populates "approximate number of records affected" in the Article 33 notification. Mark this endpoint as breach-use in documentation; access is logged separately in the audit trail as `event_type = 'breach_impact_query'`.

### Success Metrics

| Metric | Target |
|---|---|
| S3 copy event lineage recall | ≥ 90% of tables created via `s3:CopyObject` have a lineage edge within 24 hours |
| Path heuristic precision (correctly inferred parent-child) | ≥ 70% (validated by compliance analyst confirmation rate over first 90 days) |
| Cascade review completeness | 100% of tables with confirmed children are included when cascade review is triggered |
| Breach impact query latency | ≤ 5 seconds for a lineage subgraph with ≤ 50 nodes |
| Lineage graph coverage (% of tables with ≥ 1 edge) | ≥ 40% within 90 days of deployment (many tables have no detectable lineage; this is expected) |

### Risks

| Risk | Mitigation |
|---|---|
| Inferred lineage edges create false cascade review queues | Analyst confirmation step before cascade review is triggered for inferred (confidence < 1.0) edges; `confidence = 1.0` edges from `s3_copy_event` can trigger automatically |
| Recursive lineage graph for large data estates is expensive to traverse | Depth cap at 5 levels for the API response; async computation for deeper graphs with a webhook callback |
| Airflow metadata access requires Airflow API credentials | Airflow API is already accessible from the internal VPC; a service account token stored in `AIRFLOW_API_TOKEN` env var (added to `.env.example`) is sufficient |

---

## Implementation Sequencing

Features are not independent — some share infrastructure introduced by earlier features. The recommended implementation order follows the feature table but with specific dependency notes:

```
Q3 2026
  ├── F-01 (DSAR Accelerator)     — builds: new DB table, new API endpoints, new dashboard panel
  └── F-02 (Living ROPA)          — builds: DB view, PDF generation (weasyprint), new API endpoints

Q4 2026
  ├── F-03 (Slack Bot)            — requires: existing /remediate endpoint (already built)
  │                               — reuses: weasyprint and audit log patterns from F-02
  └── F-04 (Compliance Dashboard) — reuses: weasyprint from F-02, trend queries from audit_log

Q1 2027
  ├── F-05 (Catalog Discovery)    — requires: existing sampling pipeline DAG (already built)
  │                               — enables: F-06 by expanding scanner_events coverage
  └── F-06 (Lineage Tracker)      — requires: F-05 for full catalog coverage before lineage inference
                                  — requires: scanner_events.raw_event to be populated (already is)
```

**Shared infrastructure introduced in Q3 and reused in Q4:**
- `weasyprint` PDF generation (F-02 → reused in F-04, F-05 backlog report)
- `ropa_annotations` table pattern (F-02 → reused for lineage confirmations in F-06)
- Audit log `event_type` taxonomy expansion (F-01 adds `dsar_search_initiated`; F-02 adds `ropa_exported`; F-06 adds `lineage_confirmed`, `breach_impact_query`)

---

## Effort Summary

| Feature | New API Endpoints | New DB Objects | New DAGs | New Dashboard Panels | Sprints |
|---|---|---|---|---|---|
| F-01 DSAR | 3 | 1 table | 0 | 1 | 3 |
| F-02 ROPA | 4 | 1 view + 1 table | 0 | 1 card | 2 |
| F-03 Slack Bot | 2 | 1 table | 0 | 0 (Slack-side only) | 2 |
| F-04 Compliance Dashboard | 4 | 0 (views over existing) | 1 weekly digest | 1 full view | 2 |
| F-05 Catalog Discovery | 1 | 0 | 1 | 1 card | 3 |
| F-06 Lineage Tracker | 4 | 1 table | 1 daily task | 1 section + 1 view | 4 |
| **Total** | **18** | **4** | **3** | **5** | **16** |

16 sprints (32 weeks) for the full roadmap at 1 engineer per sprint. With 2 parallel engineers, the full roadmap is achievable in approximately 18–20 weeks, given F-01/F-02 can run in parallel in Q3 and F-03/F-04 can run in parallel in Q4.

---

*This roadmap was drafted at M6 milestone completion (Sprint 8). Features are ordered by business value; sequencing reflects technical dependencies. The roadmap should be reviewed at each quarterly business review and reprioritized based on: (1) DPA enforcement trends, (2) DPO feedback on which operational pain points are largest, and (3) organizational data lake growth rate (which affects the relative priority of F-05 vs. F-06).*
