# DPO Quick-Start — Privacy Risk Inventory

> One-page reference. For full documentation see [dpo-user-guide.md](dpo-user-guide.md).

---

## Login

**URL:** https://dashboard.piidetect.yourcompany.com  
**Credentials:** Your company SSO email + password

---

## Your Daily Workflow

```
1. Open Risk Inventory
2. Filter by Status = "flagged"
3. Sort by Max Confidence (highest first)
4. Click each table → review PII Report
5. Choose action: Anonymize | Quarantine | False Positive
6. Done — audit log records your decision automatically
```

---

## Status Reference

| Badge | Meaning | Your Action |
|---|---|---|
| 🔴 flagged | PII detected, awaiting action | Review and remediate |
| 🟡 quarantined | Data isolated, deletion in 30 days | Decide: delete or export |
| 🟢 remediated | Anonymized — no further action needed | None |

---

## Taking Action (from PII Report page)

| Button | What it does | Reversible? |
|---|---|---|
| **Anonymize Now** | Masks PII in place (SHA-256 email, [REDACTED] SSN) | **No** |
| **Send to Quarantine** | Moves data to isolated S3 bucket | No (data deleted in 30 days) |
| **Mark as False Positive** | Removes from inventory, improves model | Yes (contact platform) |

---

## Confidence Score Guide

| Score | Meaning |
|---|---|
| ≥ 95% | Near-certain PII — act promptly |
| 85–94% | High confidence — flagged for mandatory review |
| < 85% | Below threshold — informational only, not flagged |

---

## Exporting for Regulators

**Audit Log → Export CSV** — filters apply to the export.  
Include columns: `timestamp`, `event_type`, `table_id`, `actor`, `details`.

---

## Key Reminders

- **You will never see actual PII values** — only column names and scores
- Quarantined data is **auto-deleted after 30 days** (warning email at day 23)
- Audit log is **append-only** — every action is permanently recorded
- Scan runs every **24 hours** — new tables appear next day

---

## Contacts

| Need | Contact |
|---|---|
| Login issues | IT Helpdesk |
| System not detecting PII | Platform Engineering |
| Legal / regulatory questions | Legal & Privacy team |
