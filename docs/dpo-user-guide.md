# DPO User Guide — Privacy Risk Inventory

**Audience:** Data Protection Officers, Privacy Auditors  
**System:** Shadow Data & PII Ghost-Hunter  
**Version:** 1.0 (Sprint 8)

---

## What This System Does

The Privacy Risk Inventory automatically scans every table and file created in the company's data lake and cloud warehouses. When it detects columns that likely contain Personally Identifiable Information (PII) — email addresses, CPF numbers, credit card numbers, full names — it flags them for your review.

You do not need to configure anything. Scanning runs automatically every 24 hours. This guide explains how to use the dashboard to review findings and take action.

---

## Logging In

1. Navigate to **https://dashboard.piidetect.yourcompany.com**
2. Enter your company email and password
3. Your role (`dpo` or `auditor`) determines what actions you can take:
   - **DPO:** Can view all findings and trigger remediation (anonymize or quarantine)
   - **Auditor:** Read-only — can view all findings and export audit logs, cannot trigger remediation

If you cannot log in, contact the platform team or IT helpdesk. Do not share your credentials.

---

## The Risk Inventory Page

This is your home screen. It shows every flagged table across the data infrastructure.

### KPI Cards

At the top of the page you will see four cards:

| Card | Meaning |
|---|---|
| **Total Flagged Tables** | Tables with at least one PII column detected at confidence ≥ 85% |
| **Tables Remediated** | Tables where anonymization or quarantine has been completed |
| **Pending Review** | Tables flagged but not yet acted on — your priority queue |
| **Compliance Score** | Percentage of flagged tables that have been remediated (target: > 90%) |

### The Findings Table

Each row represents one flagged table. Columns:

| Column | Description |
|---|---|
| **Table / Source** | The database table name or S3 path |
| **Data Source** | AWS S3, Redshift, Glue, etc. |
| **PII Categories** | Types of PII detected: EMAIL, SSN, CREDIT_CARD, PHONE, etc. |
| **Max Confidence** | The model's highest confidence score for any column in this table (0–100%) |
| **Status** | `flagged` → `quarantined` or `remediated` |
| **Last Scanned** | When the classification last ran for this table |

### Filtering

Use the filter bar above the table to narrow results:
- **PII Category** — show only tables with a specific type of PII
- **Status** — show only tables pending action
- **Data Source** — focus on a specific system (e.g. `s3`, `redshift`)

---

## Investigating a Flagged Table

Click any table row to open the **PII Report** for that table.

### PII Report Page

This shows column-level detail:

- **Column name** — which specific column contains PII
- **PII Category** — what kind (EMAIL, SSN, CREDIT_CARD, etc.)
- **Confidence** — how certain the model is (shown as a bar; ≥ 85% triggers flagging)
- **Sample Count** — how many values were inspected (max 1,000; actual values are never shown)
- **Status** — current remediation state for this column

> **Privacy note:** The system never displays actual data values — only metadata.
> You will never see a real email address, SSN, or credit card number in the dashboard.

### Understanding Confidence Scores

| Confidence | Interpretation |
|---|---|
| 95–100% | Very high certainty — almost certainly PII |
| 85–94% | High certainty — flagged for mandatory review |
| 70–84% | Moderate — below threshold, shown as informational only |
| < 70% | Low — not flagged |

---

## Taking Action

### Option 1: Anonymize

Replaces PII values with irreversible anonymized equivalents:
- Emails → SHA-256 hash
- Credit cards → last 4 digits only
- SSN/CPF → `[REDACTED]`
- Full names → format-preserving pseudonym

Use this when the data still has analytical value (e.g. join keys).

**How:**
1. Open the PII Report for the table
2. Click **Anonymize Now**
3. Confirm in the dialog — note this action is irreversible
4. Status changes to `remediated` within minutes

### Option 2: Quarantine

Moves the entire table to an isolated, access-restricted S3 prefix (`s3://pii-quarantine/`). The data is preserved but inaccessible to any team except DPOs.

Use this when you are unsure whether the data should be deleted, or when the data must be preserved for a legal hold.

**How:**
1. Open the PII Report for the table
2. Click **Send to Quarantine**
3. Confirm in the dialog
4. Status changes to `quarantined` within minutes

> **Retention:** Quarantined data is automatically deleted after **30 days** unless you take further action. You will receive a warning email 7 days before automatic deletion.

### Option 3: Mark as False Positive

If the model mis-classified a column, mark it as a false positive. This removes it from the risk inventory and feeds the correction back into model retraining.

**How:**
1. Open the PII Report
2. Click **Mark as False Positive** next to the specific column
3. The column is removed from the active inventory

---

## The Audit Log

Every action — automatic or manual — is permanently recorded in the audit log.

Navigate to **Audit Log** in the left menu.

### What Is Logged

- System detections (`pii_detected`)
- Manual remediations you trigger (`manual_anonymize_requested`, `manual_quarantine_requested`)
- False positive markings
- Data exports

### Filtering the Audit Log

- **Actor** — filter by the user who took the action (your email, or `system`)
- **Event Type** — narrow to a specific action type
- **Date Range** — focus on a specific time window

### Exporting for Compliance

Click **Export CSV** to download the full filtered audit log. This export is suitable for GDPR Article 30 records and regulator inspection packages.

---

## The Data Sources Map

Navigate to **Data Sources** in the left menu to see where PII risk is concentrated.

Each row represents a data source (an S3 bucket, a Redshift cluster, etc.):

| Column | Description |
|---|---|
| **Source** | Identifier for the data system |
| **Type** | S3, Redshift, Glue, etc. |
| **Total Tables** | How many tables exist in this source |
| **Flagged Tables** | How many have detected PII |
| **Max Confidence** | Highest confidence score across all tables |
| **PII Categories** | All PII types found in this source |

Use this view to identify which data systems carry the highest risk footprint.

---

## Email Notifications

You will receive automated emails when:
- A new table with high-confidence PII is detected (confidence ≥ 85%)
- A quarantined table is 7 days from automatic deletion
- A remediation action completes (success or failure)

If you are not receiving notifications, verify your email is set correctly in the system and check your spam folder. Contact the platform team if the issue persists.

---

## Frequently Asked Questions

**Q: Can I see the actual PII values?**  
A: No. The system is designed so that PII values are never displayed or logged — only column names, categories, and confidence scores are visible.

**Q: How often does the scan run?**  
A: The patrol runs automatically every 24 hours. New tables created today will appear in the inventory by the same time tomorrow.

**Q: What if the confidence score is very high but I know it is a false positive?**  
A: Use the "Mark as False Positive" button. It removes the table from the active risk queue and improves future model accuracy.

**Q: How long does anonymization take?**  
A: For most tables, 2–15 minutes depending on size. The status will update automatically when complete.

**Q: What happens to quarantined data after 30 days?**  
A: It is automatically deleted. You will receive a warning email 7 days before deletion. If you need to extend the retention period, contact the platform team before the deadline.

**Q: Who can see the audit log?**  
A: All authenticated users (DPO, auditor, viewer). Only DPOs can trigger remediations.

---

## Getting Help

| Issue | Contact |
|---|---|
| Login problems | IT Helpdesk |
| System not detecting known PII | Platform Engineering |
| Model false positives or misclassifications | ML Engineering |
| Regulatory / legal questions about findings | Legal & Privacy team |
| Dashboard bugs | GitHub Issues |
