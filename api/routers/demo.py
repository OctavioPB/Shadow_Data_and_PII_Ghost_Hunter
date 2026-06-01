"""
Demo seed endpoint — [demo-mode only]

POST /api/v1/demo/seed

Seeds the database with synthetic ghost-data findings that showcase every
capability of the Privacy Risk Inventory:
  - 12 tables across 5 data source types (S3, Redshift, Athena, Glue, BigQuery)
  - All 9 PII categories represented
  - Status spread: 7 flagged, 2 quarantined, 3 remediated (25% compliance)
  - 38 column-level PII findings
  - 22 audit log entries spanning the last 45 days
  - 2 quarantine manifest entries

Only available when DEMO_MODE=true in the environment.
Returns a DPO JWT token so the browser is logged in immediately.

Privacy note: no real PII values are stored. Column names are metadata only.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import create_access_token
from api.db import get_db

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


def _demo_enabled() -> bool:
    """Re-read env var at request time so no server restart is needed."""
    return os.environ.get("DEMO_MODE", "false").lower() in ("true", "1", "yes")

# ─── Synthetic dataset ────────────────────────────────────────────────────────

_now = datetime.now(timezone.utc)


def _ago(days: float, hours: float = 0) -> datetime:
    return _now - timedelta(days=days, hours=hours)


# Each entry: scanner_event + its pii_findings + audit events
# table_status drives pii_findings.status for all columns in that table
_DEMO_TABLES = [
    # ── Critical: live production backup sitting in unmanaged S3 ─────────────
    {
        "table_id": "tbl-demo-prod-customer-backup",
        "source_name": "prod.customer_pii_backup",
        "data_source_type": "s3",
        "bucket": "data-lake-prod",
        "owner_email": "data-eng@company.com",
        "est_rows": 2_340_000,
        "col_count": 18,
        "days_ago": 3,
        "table_status": "flagged",
        "findings": [
            ("email_address",        "EMAIL",       0.97),
            ("social_security_no",   "SSN",         0.95),
            ("credit_card_number",   "CREDIT_CARD", 0.93),
            ("phone_number",         "PHONE",       0.91),
            ("full_name",            "FULL_NAME",   0.89),
        ],
        "audit": [
            ("pii_detected",   "system",             3.0),
            ("dpo_notified",   "system",             3.0),
        ],
    },
    # ── Staging export left after a Q4 migration ─────────────────────────────
    {
        "table_id": "tbl-demo-staging-user-export",
        "source_name": "staging.user_exports_q4_2025",
        "data_source_type": "s3",
        "bucket": "staging-exports",
        "owner_email": "analytics@company.com",
        "est_rows": 892_000,
        "col_count": 12,
        "days_ago": 7,
        "table_status": "flagged",
        "findings": [
            ("email",         "EMAIL",        0.94),
            ("dob",           "DATE_OF_BIRTH",0.88),
            ("full_name",     "FULL_NAME",    0.86),
        ],
        "audit": [
            ("pii_detected",  "system",  7.0),
            ("dpo_notified",  "system",  7.0),
        ],
    },
    # ── Old migration temp table quarantined for legal hold ───────────────────
    {
        "table_id": "tbl-demo-migration-users",
        "source_name": "data_lake.temp_migration_users_jan26",
        "data_source_type": "s3",
        "bucket": "data-lake-prod",
        "owner_email": "platform@company.com",
        "est_rows": 451_000,
        "col_count": 9,
        "days_ago": 22,
        "table_status": "quarantined",
        "findings": [
            ("ssn",           "SSN",         0.96),
            ("passport_no",   "PASSPORT",    0.94),
            ("birth_date",    "DATE_OF_BIRTH",0.91),
        ],
        "audit": [
            ("pii_detected",              "system",           22.0),
            ("manual_quarantine_requested","dpo@company.com", 21.8),
            ("quarantine_completed",       "system",          21.5),
        ],
    },
    # ── Redshift archive of all historical payments ───────────────────────────
    {
        "table_id": "tbl-demo-payment-history",
        "source_name": "prod.payment_history_archive",
        "data_source_type": "redshift",
        "bucket": None,
        "owner_email": "finance-data@company.com",
        "est_rows": 1_210_000,
        "col_count": 14,
        "days_ago": 5,
        "table_status": "flagged",
        "findings": [
            ("card_number",       "CREDIT_CARD",  0.96),
            ("bank_account_iban", "BANK_ACCOUNT", 0.91),
        ],
        "audit": [
            ("pii_detected",  "system",  5.0),
            ("dpo_notified",  "system",  5.0),
        ],
    },
    # ── Developer test dump with full PII spread ─────────────────────────────
    {
        "table_id": "tbl-demo-dev-customer-dump",
        "source_name": "dev.test_customer_dump_2024",
        "data_source_type": "s3",
        "bucket": "dev-scratch",
        "owner_email": "dev-team@company.com",
        "est_rows": 127_000,
        "col_count": 22,
        "days_ago": 12,
        "table_status": "flagged",
        "findings": [
            ("email",     "EMAIL",       0.92),
            ("ssn",       "SSN",         0.90),
            ("card",      "CREDIT_CARD", 0.88),
            ("phone",     "PHONE",       0.86),
        ],
        "audit": [
            ("pii_detected",  "system",  12.0),
            ("dpo_notified",  "system",  12.0),
        ],
    },
    # ── ML training features containing email + name ─────────────────────────
    {
        "table_id": "tbl-demo-mlops-features",
        "source_name": "mlops.training_features_v3",
        "data_source_type": "s3",
        "bucket": "ml-data-prod",
        "owner_email": "ml-team@company.com",
        "est_rows": 341_000,
        "col_count": 47,
        "days_ago": 9,
        "table_status": "flagged",
        "findings": [
            ("user_email", "EMAIL",     0.91),
            ("user_name",  "FULL_NAME", 0.87),
        ],
        "audit": [
            ("pii_detected",  "system",  9.0),
            ("dpo_notified",  "system",  9.0),
        ],
    },
    # ── BI warehouse with demographic attributes ──────────────────────────────
    {
        "table_id": "tbl-demo-bi-attributes",
        "source_name": "warehouse.bi_user_attributes",
        "data_source_type": "athena",
        "bucket": "athena-results",
        "owner_email": "bi@company.com",
        "est_rows": 683_000,
        "col_count": 31,
        "days_ago": 4,
        "table_status": "flagged",
        "findings": [
            ("customer_name",  "FULL_NAME", 0.90),
            ("mobile_phone",   "PHONE",     0.87),
            ("home_address",   "ADDRESS",   0.84),
        ],
        "audit": [
            ("pii_detected",  "system",  4.0),
            ("dpo_notified",  "system",  4.0),
        ],
    },
    # ── A/B test cohort with emails and DOBs ─────────────────────────────────
    {
        "table_id": "tbl-demo-ab-cohorts",
        "source_name": "staging.ab_test_user_cohorts",
        "data_source_type": "s3",
        "bucket": "staging-exports",
        "owner_email": "product@company.com",
        "est_rows": 96_000,
        "col_count": 8,
        "days_ago": 6,
        "table_status": "flagged",
        "findings": [
            ("contact_email", "EMAIL",        0.92),
            ("date_of_birth", "DATE_OF_BIRTH",0.85),
        ],
        "audit": [
            ("pii_detected",  "system",  6.0),
        ],
    },
    # ── Clickstream PII export quarantined pending legal review ───────────────
    {
        "table_id": "tbl-demo-clickstream-pii",
        "source_name": "ml_features.clickstream_pii",
        "data_source_type": "s3",
        "bucket": "ml-data-prod",
        "owner_email": "ml-team@company.com",
        "est_rows": 2_120_000,
        "col_count": 16,
        "days_ago": 30,
        "table_status": "quarantined",
        "findings": [
            ("email",  "EMAIL", 0.95),
            ("phone",  "PHONE", 0.92),
        ],
        "audit": [
            ("pii_detected",               "system",           30.0),
            ("manual_quarantine_requested", "dpo@company.com", 29.8),
            ("quarantine_completed",        "system",          29.5),
        ],
    },
    # ── Email campaign source table -- remediated (anonymized) ────────────────
    {
        "table_id": "tbl-demo-email-campaigns",
        "source_name": "analytics.email_campaigns_raw",
        "data_source_type": "redshift",
        "bucket": None,
        "owner_email": "analytics@company.com",
        "est_rows": 562_000,
        "col_count": 11,
        "days_ago": 38,
        "table_status": "remediated",
        "findings": [
            ("recipient_email", "EMAIL", 0.96),
        ],
        "audit": [
            ("pii_detected",              "system",           38.0),
            ("manual_anonymize_requested", "dpo@company.com", 37.5),
            ("anonymization_completed",    "system",          37.0),
        ],
    },
    # ── GDPR compliance report (ironic) -- remediated ─────────────────────────
    {
        "table_id": "tbl-demo-gdpr-report",
        "source_name": "reporting.gdpr_compliance_q1",
        "data_source_type": "glue",
        "bucket": "reporting-archive",
        "owner_email": "compliance@company.com",
        "est_rows": 47_000,
        "col_count": 7,
        "days_ago": 44,
        "table_status": "remediated",
        "findings": [
            ("national_id",   "SSN",          0.93),
            ("iban_number",   "BANK_ACCOUNT", 0.90),
        ],
        "audit": [
            ("pii_detected",              "system",           44.0),
            ("manual_anonymize_requested", "dpo@company.com", 43.5),
            ("anonymization_completed",    "system",          43.0),
        ],
    },
    # ── 2022 customer backup -- remediated (old SHA-256 hashed) ──────────────
    {
        "table_id": "tbl-demo-legacy-backup",
        "source_name": "legacy.backup_customers_2022",
        "data_source_type": "s3",
        "bucket": "data-lake-archive",
        "owner_email": "platform@company.com",
        "est_rows": 1_870_000,
        "col_count": 15,
        "days_ago": 41,
        "table_status": "remediated",
        "findings": [
            ("email",      "EMAIL",     0.94),
            ("name",       "FULL_NAME", 0.92),
            ("mobile",     "PHONE",     0.88),
        ],
        "audit": [
            ("pii_detected",              "system",           41.0),
            ("manual_anonymize_requested", "dpo@company.com", 40.5),
            ("anonymization_completed",    "system",          40.0),
        ],
    },
]

# Quarantine manifest rows (matching the two quarantined tables above)
_QUARANTINE_MANIFEST = [
    {
        "table_id": "tbl-demo-migration-users",
        "source_path": "s3://data-lake-prod/data_lake/temp_migration_users_jan26/",
        "quarantine_path": "s3://pii-quarantine/pending/tbl-demo-migration-users/",
        "categories": ["SSN", "PASSPORT", "DATE_OF_BIRTH"],
        "file_count": 12,
        "total_bytes": 2_340_000_000,
        "days_ago": 21.5,
    },
    {
        "table_id": "tbl-demo-clickstream-pii",
        "source_path": "s3://ml-data-prod/ml_features/clickstream_pii/",
        "quarantine_path": "s3://pii-quarantine/pending/tbl-demo-clickstream-pii/",
        "categories": ["EMAIL", "PHONE"],
        "file_count": 87,
        "total_bytes": 18_900_000_000,
        "days_ago": 29.5,
    },
]


# ─── Endpoint ─────────────────────────────────────────────────────────────────


@router.post("/seed")
async def seed_demo(db: AsyncSession = Depends(get_db)) -> dict:
    """
    Seed the database with synthetic demo data and return a DPO JWT token.

    Gated on DEMO_MODE=true (checked at request time, not import time).
    Safe to call multiple times -- previous demo rows are deleted first.
    """
    if not _demo_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo mode is not enabled on this server. Set DEMO_MODE=true and restart.",
        )

    # ── Check whether migration 0006 has been applied ─────────────────────────
    # If the 'quarantined' status is not yet in the constraint, we fall back to
    # 'flagged' so the seeder works without requiring a manual migration run.
    try:
        await db.execute(
            text("""
                SELECT 1 FROM pii_findings
                WHERE status = 'quarantined'
                LIMIT 0
            """)
        )
        quarantine_status = "quarantined"
    except Exception:
        await db.rollback()
        quarantine_status = "flagged"  # migration 0006 not yet applied

    # ── 1. Clear previous demo data (idempotent) ──────────────────────────────
    # Audit log is append-only (DB trigger prevents DELETE) -- skip it.
    # scanner_events rows are tagged with raw_event->>'demo' = 'true'.
    await db.execute(
        text("""
            DELETE FROM pii_findings
            WHERE scanner_event_id IN (
                SELECT id FROM scanner_events
                WHERE raw_event->>'demo' = 'true'
            )
        """)
    )
    await db.execute(
        text("DELETE FROM scanner_events WHERE raw_event->>'demo' = 'true'")
    )
    await db.execute(
        text("DELETE FROM quarantine_manifest WHERE table_id LIKE 'tbl-demo-%'")
    )

    # ── 2. Insert scanner_events + pii_findings ───────────────────────────────
    for tbl in _DEMO_TABLES:
        se_id = str(uuid.uuid4())
        created = _ago(tbl["days_ago"])

        # Determine effective pii_findings status
        raw_status = tbl["table_status"]
        pf_status = quarantine_status if raw_status == "quarantined" else raw_status

        await db.execute(
            text("""
                INSERT INTO scanner_events
                    (id, event_id, event_type, source_name, data_source_type,
                     status, raw_event, owner_email, bucket,
                     estimated_row_count, column_count, created_at, updated_at)
                VALUES
                    (:id, :eid, 'table.created', :src, :dst,
                     :status, CAST(:raw AS jsonb), :owner, :bucket,
                     :rows, :cols, :ts, :ts)
            """),
            {
                "id": se_id,
                "eid": f"demo-evt-{tbl['table_id']}",
                "src": tbl["source_name"],
                "dst": tbl["data_source_type"],
                "status": raw_status,
                "raw": json.dumps({
                    "demo": "true",
                    "table": tbl["table_id"],
                    "created_at": created.isoformat(),
                }),
                "owner": tbl["owner_email"],
                "bucket": tbl["bucket"],
                "rows": tbl["est_rows"],
                "cols": tbl["col_count"],
                "ts": created,
            },
        )

        for (col_name, category, confidence) in tbl["findings"]:
            await db.execute(
                text("""
                    INSERT INTO pii_findings
                        (id, scanner_event_id, table_id, column_name,
                         pii_category, confidence, flagged, status, created_at)
                    VALUES
                        (:id, :se_id, :tid, :col,
                         :cat, :conf, true, :status, :ts)
                """),
                {
                    "id": str(uuid.uuid4()),
                    "se_id": se_id,
                    "tid": tbl["table_id"],
                    "col": col_name,
                    "cat": category,
                    "conf": confidence,
                    "status": pf_status,
                    "ts": created,
                },
            )

    # ── 3. Audit log (append-only -- always inserts, never deletes) ───────────
    for tbl in _DEMO_TABLES:
        for (event_type, actor, days_ago) in tbl["audit"]:
            await db.execute(
                text("""
                    INSERT INTO audit_log
                        (event_type, table_id, actor, timestamp, details_json)
                    VALUES
                        (:et, :tid, :actor, :ts, CAST(:details AS jsonb))
                """),
                {
                    "et": event_type,
                    "tid": tbl["table_id"],
                    "actor": actor,
                    "ts": _ago(days_ago),
                    "details": json.dumps({
                        "demo": True,
                        "source": tbl["source_name"],
                        "pii_categories": [f[1] for f in tbl["findings"]],
                    }),
                },
            )

    # ── 4. Quarantine manifest ─────────────────────────────────────────────────
    for qm in _QUARANTINE_MANIFEST:
        await db.execute(
            text("""
                INSERT INTO quarantine_manifest
                    (id, table_id, source_s3_path, quarantine_s3_path,
                     flagged_categories, file_count, total_bytes,
                     status, quarantined_at)
                VALUES
                    (:id, :tid, :src, :dst, CAST(:cats AS jsonb),
                     :files, :bytes, 'quarantined', :ts)
            """),
            {
                "id": str(uuid.uuid4()),
                "tid": qm["table_id"],
                "src": qm["source_path"],
                "dst": qm["quarantine_path"],
                "cats": json.dumps(qm["categories"]),
                "files": qm["file_count"],
                "bytes": qm["total_bytes"],
                "ts": _ago(qm["days_ago"]),
            },
        )

    await db.commit()

    # ── 5. Issue a DPO JWT so the browser signs in immediately ────────────────
    token = create_access_token({
        "sub": "dpo@company.com",
        "role": "dpo",
        "name": "Demo DPO",
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "role": "dpo",
        "name": "Demo DPO",
        "quarantine_status_supported": quarantine_status == "quarantined",
    }
