# Business Decisions

This page sums up the business case and the product decisions that shape the platform: what problem it solves, for whom, what it deliberately does and does not do, and how success is judged. The full analysis (market sizing, personas, the regulatory deep-dive and the glossary) is in [planning/BUSINESS.md](../planning/BUSINESS.md). The figures here come from that document.

---

## 1. The problem

**Shadow data** means copies of personal data that nobody is accountable for, such as:
- `customers_backup_2023` in a staging bucket;
- an analyst's CSV export;
- a temporary Athena table that outlived its purpose.

Shadow data accumulates through routine engineering work. It sits outside the ROPA (Records of Processing Activities), has no documented legal basis, and widens the scope of any breach.

Under GDPR, fines reach **4% of global turnover or €20M**. A breach must be notified within **72 hours**, which is impossible to scope if the organisation does not know where its copies are. LGPD (Brazil) adds similar obligations. Manual audits are periodic, expensive, and outdated as soon as they are finished.

**Decision: build continuous, event-driven detection, not periodic audits.** Every new table or file is inspected within 24 hours of creation, so the exposure window is a day, not a quarter.

---

## 2. Who it is for

| Role | What they need | What the platform gives them |
|---|---|---|
| **DPO / Chief Compliance Officer** (economic buyer) | Evidence for regulators and the board | Risk inventory, compliance score, ROPA export, append-only audit trail |
| **Compliance analyst** (daily user) | A work queue instead of spreadsheets | Flagged tables with per-column reports, DSAR search, remediation requests |
| **CDO / VP Data Engineering** (technical buyer) | No new toil for engineers | Automatic anonymization and quarantine for S3; integration with existing Kafka and Airflow |
| **Platform engineer** (champion) | Something that runs itself | Idempotent DAGs, runbooks, Grafana dashboards and alerts |
| **Legal / InfoSec** (potential blocker) | Assurance that the tool is not a new risk | No PII stored; metadata only; non-root, scanned images; OWASP review |

---

## 3. Product decisions

### BD-01: Build, not buy

**Decision.** Build on the existing AWS + Kafka + Airflow + Python stack instead of licensing a commercial DLP/discovery tool such as BigID or Securiti.

**Why.**
- **Cost.** The estimated 3-year TCO is **€427,800**, against **€620,000–€770,000** for a commercial tool covering 50 data sources and 5 seats. That saves about **€190k–€340k** over 3 years.
- **Data never leaves the perimeter.** A commercial SaaS scanner would itself need a DPIA and a processor agreement.
- **Deeper integration.** The platform reacts to the organisation's own Kafka events instead of crawling on a schedule.
- **Multilingual coverage.** Detection works in EN/ES/PT, including local identifiers (CPF, CURP).

**Trade-off.** Ongoing maintenance (about 1 engineer-day per week) and no vendor support. The approach can also be copied, so the advantage is the accumulated detection history and fine-tuning, not the code.

### BD-02: Automatic remediation only where it is safe

**Decision.** Only **S3 objects in known formats** (parquet, csv, json) are remediated automatically. Glue/Athena catalog tables are flagged and reported to the DPO, not moved.

**Why.** Moving an object in S3 is reversible and has a well-defined blast radius. Rewriting a catalog table can break downstream jobs, views and contracts the platform cannot see. A false positive that breaks a production pipeline would cost more trust than any fine it prevents.

**Consequence.** Catalog tables need human follow-up. Track them as their own queue (see KPIs).

### BD-03: Quarantine, never delete on detection

**Decision.** Flagged raw data is **moved** to an isolated, encrypted quarantine bucket, never deleted on detection. It is deleted automatically after **30 days**, and the owner is warned **7 days** before.

**Why.**
- A model-driven deletion cannot be undone. Quarantine leaves a recovery path for false positives and for data under legal hold.
- 30 days is a commonly accepted reading of "without undue delay" for erasure (GDPR Art. 17; LGPD non-retention). It is long enough for DPO review during holidays or peak periods, and short enough that quarantine does not turn into storage.
- The 7-day warning gives owners time to claim a legitimate dataset before it is gone.

**Safeguard.** Quarantine runs **even if anonymization fails**: raw PII is always contained, even when the anonymized copy cannot be produced.

### BD-04: Anonymize so the data stays useful

**Decision.** Each PII category gets its own transformation instead of blanket redaction:
- emails are hashed, so joins still work;
- cards keep their last 4 digits (PCI-DSS practice);
- dates of birth keep only the year, for age cohorts;
- addresses keep city and state;
- SSN, bank account and passport are redacted in full.

**Why.** If anonymization destroys analytical value, teams route around the tool and recreate the shadow copy. Keeping the useful part removes the incentive.

**Accepted trade-off.** Pseudonymised full names cannot be recognised as already-anonymized without risking real names passing through unchanged. So idempotency for names is enforced at the job level, not value by value. Privacy is preferred over re-run convenience.

### BD-05: Confidence threshold of 0.85

**Decision.** A column is flagged when the classifier's confidence is **≥ 0.85**. The threshold is configurable with `MODEL_CONFIDENCE_THRESHOLD`.

**Why.** The two errors do not cost the same:
- A false negative leaves PII exposed, which is a regulatory risk.
- A false positive costs an analyst a few minutes, and quarantine makes it reversible.

0.85 aims for high recall while keeping the review queue manageable. The quality targets are a false positive rate ≤ 10% and macro F1 ≥ 0.90. Revisit the threshold once real precision/recall figures exist (see [results.md](results.md#3-model)).

### BD-06: Metadata only, never PII values

**Decision.** The platform stores table names, column names, categories and confidence scores. **Never the PII values.**
- Sample rows expire from the staging bucket after 7 days.
- DSAR search identifiers are stored as keyed HMACs.
- Logs are tested to contain no values.

**Why.** A compliance tool that holds a copy of every PII column becomes the most valuable breach target in the company, and its own shadow-data problem. This is also what makes InfoSec and Legal approval achievable.

### BD-07: Evidence first: an append-only audit trail

**Decision.** Every detection, remediation, annotation and DSAR search is written to `audit_log`. A database trigger blocks updates and deletes.

**Why.** Under GDPR's accountability principle (Art. 5(2)), the organisation must *demonstrate* compliance, not just achieve it. An audit trail that cannot be edited is what makes the platform's output admissible as evidence in a DPA inspection.

### BD-08: Support ROPA and DSAR, but don't replace legal judgment

**Decision.** The platform fills in the facts for ROPA (GDPR Art. 30): which data, where, which categories, when it was first seen. The **DPO** annotates purpose, legal basis and cross-border transfer. DSAR search (Art. 15) finds which tables may hold a person's data. It does not decide whether to disclose or erase.

**Why.** The legal basis and the purpose are legal determinations. Automating them would create false confidence. The ROPA export marks undocumented fields as `(not documented)` so gaps are visible rather than hidden.

### BD-09: Multilingual from day one

**Decision.** The model covers English, Spanish and Portuguese, with locale-specific formats (US SSN, Brazilian CPF, Mexican CURP and phone/address formats).

**Why.** The target market is organisations under GDPR and LGPD with Latin American operations. People-analytics data (HR, payroll) is the highest-risk PII category, and it is usually local-language and local-format.

### BD-10: Development user store first, real identity later

**Decision.** Ship with a static, environment-configurable user store and four roles (dpo, admin, auditor, viewer).

**Why.** It unblocks internal proof of value without waiting for an SSO integration.

**Constraint.** It must be replaced by the corporate IdP (SSO, with MFA for DPO and admin) before any production use. This is listed as a known limitation in the README.

---

## 4. Economics

From [planning/BUSINESS.md §13–14](../planning/BUSINESS.md#13-total-cost-of-ownership).

| Item | Estimate |
|---|---|
| Year 1 (build + infrastructure) | €232,600: €160,000 engineering + €72,600 AWS |
| Years 2–3 (steady state) | €97,600/year: €25,000 maintenance + €72,600 AWS |
| 3-year TCO | **€427,800** |
| 3-year commercial alternative | €620,000–€770,000 |
| Annual expected value, conservative | €197,550: €180,000 expected fine avoidance + €17,550 audit labour |
| 3-year ROI, conservative | **38.5%**, payback in 2.2 years |
| 3-year ROI, one €2M fine avoided | **506%** |

**Biggest cost lever.** The infrastructure estimate assumes GPU nodes for inference. The service runs on CPU by default (`MODEL_DEVICE=-1`). If CPU latency stays under the 2 s p95 target, moving to CPU nodes cuts monthly infrastructure from about €6,050 to about €4,200. Measuring that latency is the cheapest way to improve the ROI.

**Sensitivity.** ROI depends mostly on how likely a fine is (lower if the organisation already has a mature data catalog) and how large it would be (higher when shadow tables hold millions of records).

---

## 5. Rollout

| Phase | Scope | Exit criterion |
|---|---|---|
| 1. Internal proof of value | One AWS account; Kafka-connected S3 and Glue sources | Detection coverage ≥ 99%; DPO uses the dashboard weekly |
| 2. Adjacent sources | More accounts; non-Kafka sources via a polling adapter | No unmonitored data-lake account |
| 3. DPO workflow | Ticketing integration, DPIA triggers, breach evidence package | Compliance score reported to the board quarterly |

Adoption risks and mitigations:
- **Engineers fear broken pipelines.** Only S3 is remediated automatically, and every action is reversible for 30 days.
- **DPOs distrust the model.** Each finding shows per-column evidence and confidence, and the DPO can mark it as a false positive from the dashboard.

---

## 6. Success metrics

| KPI | Target | For |
|---|---|---|
| Detection coverage (new assets inspected within 24 h) | ≥ 99% | DPO |
| Compliance score (flagged tables remediated) | ≥ 90% | DPO, board |
| Median time to remediation | ≤ 5 business days | DPO |
| False positive rate | ≤ 10% | DPO, model owner |
| Audit-log completeness | 100% | Auditor |
| API / inference p95 latency | ≤ 500 ms / ≤ 2 s | Platform |
| Patrol DAG success rate | ≥ 99.5% | Platform |
| Tables with unknown PII status | Trending to 0 | Board |
| DPA findings related to undiscovered PII | 0 | Board |

The **compliance score** is the headline board metric. It is bounded (0–100%) and directional, so a non-technical audience can read it at a glance. Current measurement status: [results.md §6](results.md#6-not-yet-measured).

---

## 7. Business risks

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| Model accuracy drifts on real data | Medium / Medium | Validate on labelled real columns before production; track the false positive rate; blue/green model rollout |
| Sources outside Kafka go unmonitored | Medium / Medium | Polling adapter (phase 2); "tables with unknown status" KPI |
| Regulation changes (new categories, shorter deadlines) | Medium / High | Configurable thresholds and retention; categories defined in one place |
| Limited adoption beyond the first team | Low / High | Automatic remediation for safe cases; dashboard built around the DPO's existing tasks |
| The platform itself becomes a target | Low / Critical | Stores no PII values; HMAC'd DSAR identifiers; RBAC; scanned images; append-only audit |

---

## 8. Out of scope (for now)

- Automatic remediation of catalog tables, databases outside the data lake, and SaaS tools.
- Deciding legal basis or purpose. That stays with the DPO.
- Erasure across backups and downstream systems for Art. 17 requests. The platform locates data; it does not erase it everywhere.
- PII inside unstructured documents (PDFs, emails, images).

Roadmap: [planning/ROADMAP.md](../planning/ROADMAP.md).
