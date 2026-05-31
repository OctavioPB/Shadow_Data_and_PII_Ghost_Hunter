# DPO Onboarding Checklist

**Onboardee:** ___________________________  
**Onboarding Date:** ___________________________  
**Completed by (platform team):** ___________________________

Complete all items before the onboardee's first independent use of the Privacy Risk Inventory.

---

## 1. Account & Access

- [ ] Create user account in identity provider (SSO)
- [ ] Assign role `dpo` in the PII Ghost-Hunter system
- [ ] Verify login at https://dashboard.piidetect.yourcompany.com
- [ ] Confirm role displayed in the top-right nav shows "Data Protection Officer"
- [ ] Grant access to the `pii-quarantine` S3 bucket via IAM role assignment
- [ ] Add user to DPO distribution list for automated email notifications

**Platform engineer sign-off:** _______________ Date: ___________

---

## 2. Notification Settings

- [ ] Verify DPO receives test alert email: trigger a test notification from the platform team
- [ ] Confirm email is not landing in spam / quarantine folder
- [ ] Set up Slack notification preferences (optional): connect Slack webhook to personal channel
- [ ] Verify escalation contact is configured in PagerDuty for critical alerts

**DPO sign-off:** _______________ Date: ___________

---

## 3. Walkthrough Session

Conduct a 45-minute guided session covering:

- [ ] **Risk Inventory overview** — KPI cards, table filters, status badges
- [ ] **PII Report drill-down** — column findings, confidence bars, what sample count means
- [ ] **Anonymization flow** — demonstrate on a test table; observe status change
- [ ] **Quarantine flow** — demonstrate on a test table; explain 30-day retention clock
- [ ] **False positive marking** — when and how to use it
- [ ] **Audit log** — filtering, exporting CSV for regulators
- [ ] **Data Sources map** — interpreting the risk heatmap
- [ ] **What the system does NOT show** — confirm understanding that PII values are never displayed

**Walkthrough completed by:** _______________ Date: ___________  
**DPO acknowledges understanding:** _______________ Date: ___________

---

## 4. Documentation Handoff

- [ ] Share link to [DPO User Guide](dpo-user-guide.md)
- [ ] Share [DPO Quick-Start](dpo-quickstart.md) (print or bookmark)
- [ ] Share [Data Retention Policy](data-retention-policy.md) — especially quarantine expiry rules
- [ ] Share runbooks directory link (for awareness, not expected to operate independently)

---

## 5. First Independent Review

Within 5 business days of onboarding, the DPO completes their first independent review:

- [ ] Log in independently and navigate to the Risk Inventory
- [ ] Review at least one flagged table end-to-end
- [ ] Take at least one action (anonymize, quarantine, or false positive)
- [ ] Export the audit log for the current week as a CSV
- [ ] Confirm understanding of the 30-day quarantine expiry by locating any quarantined tables

**DPO confirms first independent review:** _______________ Date: ___________

---

## 6. Emergency Contacts

Ensure the DPO has saved these contacts:

| Role | Name | Contact |
|---|---|---|
| Platform Engineering on-call | | PagerDuty: pii-ghost-hunter-api service |
| ML Engineering lead | | |
| Legal & Privacy team | | |
| IT Helpdesk | | helpdesk@company.com |

---

## Onboarding Complete

All items above completed: **Yes / No**

Platform team sign-off: _______________ Date: ___________  
DPO sign-off: _______________ Date: ___________

> File this completed checklist in the DPO onboarding folder per your records management policy.
