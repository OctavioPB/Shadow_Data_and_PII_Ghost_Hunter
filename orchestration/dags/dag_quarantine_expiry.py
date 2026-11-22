"""
Quarantine Expiry DAG — [S8-04]

Enforces the 30-day data retention policy on the quarantine S3 bucket.

Schedule: daily at 06:00 UTC

Policy:
  - Objects quarantined > 23 days: DPO warning email sent (7-day notice before auto-delete)
  - Objects quarantined > 30 days: moved to /expired/ prefix then deleted by S3 lifecycle rule

This DAG operates only on the quarantine manifest table and S3 object metadata.
It never reads or logs the content of quarantined data.

Idempotency: re-running on the same day sends no duplicate warnings and moves
no already-expired objects (checked via quarantine_manifest.warning_sent_at
and quarantine_manifest.expired_at, added in migration 0008).

Tasks are defined at module level (and wired inside the DAG function) so each
callable can be unit-tested directly through ``<task>.function``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook

log = logging.getLogger(__name__)

_DB_CONN = "pii_hunter_db"
_DPO_EMAIL = os.environ.get("DPO_EMAIL", "")
_SMTP_HOST = os.environ.get("SMTP_HOST", "")
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER = os.environ.get("SMTP_USER", "")
_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
_FROM_EMAIL = os.environ.get("NOTIFICATION_FROM_EMAIL", "noreply@company.com")
_S3_QUARANTINE_BUCKET = os.environ.get("S3_QUARANTINE_BUCKET", "pii-quarantine")
_QUARANTINE_DAYS = int(os.environ.get("QUARANTINE_RETENTION_DAYS", "30"))
_WARNING_DAYS_BEFORE = int(os.environ.get("QUARANTINE_WARNING_DAYS_BEFORE", "7"))

# Entries still held in the quarantine bucket (released/anonymized ones are not).
# make_interval() takes the day count as a typed parameter instead of splicing
# it into an INTERVAL string literal.
_ACTIVE_SQL = """
    FROM quarantine_manifest qm
    WHERE qm.expired_at IS NULL
      AND qm.status IN ('quarantined', 'under_review')
      AND qm.quarantined_at <= NOW() - make_interval(days => %s)
"""

# Owner comes from the scanner event that produced the findings.
_OWNER_SQL = """
    (SELECT se.owner_email
       FROM pii_findings pf
       JOIN scanner_events se ON se.id = pf.scanner_event_id
      WHERE pf.table_id = qm.table_id AND se.owner_email IS NOT NULL
      LIMIT 1)
"""


def _as_utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


@task
def find_expiring_soon() -> list[dict]:
    """
    Returns quarantine records that are >= (QUARANTINE_DAYS - WARNING_DAYS_BEFORE)
    old and have not yet had a warning sent.
    """
    hook = PostgresHook(postgres_conn_id=_DB_CONN)
    warning_threshold = _QUARANTINE_DAYS - _WARNING_DAYS_BEFORE
    rows = hook.get_records(
        f"""
        SELECT qm.table_id, qm.quarantine_s3_path, qm.quarantined_at, {_OWNER_SQL}
        {_ACTIVE_SQL}
          AND qm.warning_sent_at IS NULL
        ORDER BY qm.quarantined_at ASC
        """,
        parameters=(warning_threshold,),
    )
    log.info("Found %d quarantine entries approaching expiry", len(rows))
    now = datetime.now(tz=timezone.utc)
    return [
        {
            "table_id": str(r[0]),
            "s3_path": str(r[1]),
            "quarantined_at": _as_utc(r[2]).isoformat(),
            "owner_email": str(r[3]) if r[3] else None,
            "days_remaining": max(_QUARANTINE_DAYS - (now - _as_utc(r[2])).days, 0),
        }
        for r in rows
    ]


@task
def send_expiry_warnings(expiring: list[dict]) -> list[str]:
    """
    Sends DPO warning emails for each expiring quarantine entry.
    Returns table_ids for which warnings were actually delivered.
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    if not expiring:
        return []
    if not (_SMTP_HOST and _DPO_EMAIL):
        # Do not mark warnings as sent when nothing could be delivered.
        log.warning("SMTP_HOST/DPO_EMAIL not configured — %d warnings not sent", len(expiring))
        return []

    warned_table_ids: list[str] = []
    for entry in expiring:
        table_id = entry["table_id"]
        days_remaining = entry["days_remaining"]
        delete_on = (
            datetime.fromisoformat(entry["quarantined_at"]) + timedelta(days=_QUARANTINE_DAYS)
        ).date()

        subject = f"[PII Ghost-Hunter] Quarantine Expiry Warning: {table_id}"
        body = f"""
Data in quarantine is scheduled for automatic deletion in {days_remaining} day(s).

Table ID:       {table_id}
S3 Path:        {entry['s3_path']}
Quarantined at: {entry['quarantined_at']}
Auto-delete on: {delete_on}

ACTION REQUIRED if you wish to retain this data beyond the {_QUARANTINE_DAYS}-day window:
  1. Log in to the Privacy Risk Inventory dashboard
  2. Contact the platform team to extend the retention window
  3. Or export the data before the deletion date

If no action is taken, the data will be permanently deleted in {days_remaining} day(s).

This is an automated notification from the PII Ghost-Hunter system.
Do not reply to this email.
        """.strip()

        recipients = [_DPO_EMAIL]
        if entry.get("owner_email") and entry["owner_email"] != _DPO_EMAIL:
            recipients.append(entry["owner_email"])

        try:
            msg = MIMEMultipart()
            msg["From"] = _FROM_EMAIL
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=15) as server:
                server.ehlo()
                if server.has_extn("starttls"):
                    server.starttls()
                    server.ehlo()
                if _SMTP_USER:
                    server.login(_SMTP_USER, _SMTP_PASSWORD)
                server.sendmail(_FROM_EMAIL, recipients, msg.as_string())

            log.info("Expiry warning sent for table_id=%s", table_id)
            warned_table_ids.append(table_id)
        except Exception as exc:  # noqa: BLE001 — one failure must not stop the batch
            log.error("Failed to send warning for table_id=%s: %s", table_id, exc)

    return warned_table_ids


@task
def mark_warnings_sent(table_ids: list[str]) -> None:
    """Update quarantine_manifest.warning_sent_at for successfully warned entries."""
    if not table_ids:
        log.info("No warnings to mark")
        return

    hook = PostgresHook(postgres_conn_id=_DB_CONN)
    hook.run(
        """
        UPDATE quarantine_manifest
        SET warning_sent_at = NOW()
        WHERE table_id = ANY(%s)
          AND expired_at IS NULL
          AND warning_sent_at IS NULL
        """,
        parameters=(list(table_ids),),
    )
    log.info("Marked warning_sent_at for %d entries", len(table_ids))


@task
def find_expired() -> list[dict]:
    """
    Returns quarantine records that have exceeded the retention window
    and have not yet been marked as expired.
    """
    hook = PostgresHook(postgres_conn_id=_DB_CONN)
    rows = hook.get_records(
        f"""
        SELECT qm.table_id, qm.quarantine_s3_path, qm.quarantined_at
        {_ACTIVE_SQL}
        ORDER BY qm.quarantined_at ASC
        """,
        parameters=(_QUARANTINE_DAYS,),
    )
    log.info("Found %d quarantine entries past retention window", len(rows))
    return [
        {
            "table_id": str(r[0]),
            "s3_path": str(r[1]),
            "quarantined_at": _as_utc(r[2]).isoformat(),
        }
        for r in rows
    ]


@task
def move_to_expired_prefix(expired: list[dict]) -> list[str]:
    """
    Moves every object under each entry's pending/ prefix to the expired/ prefix
    (copy + delete). The S3 lifecycle rule on expired/ deletes within 24h.

    quarantine_s3_path is a *prefix* (s3://bucket/pending/<table_id>/), so its
    objects must be listed — copying the prefix itself as a key always failed
    with NoSuchKey.

    Privacy: only S3 key paths are logged — never object contents.
    """
    import boto3

    s3 = boto3.client("s3")
    expired_table_ids: list[str] = []
    bucket_uri = f"s3://{_S3_QUARANTINE_BUCKET}/"

    for entry in expired:
        table_id = entry["table_id"]
        s3_path = entry["s3_path"]

        if not s3_path.startswith(bucket_uri + "pending/"):
            log.error("Unexpected S3 path for table_id=%s: %s — skipping", table_id, s3_path)
            continue

        prefix = s3_path.removeprefix(bucket_uri).rstrip("/") + "/"
        try:
            paginator = s3.get_paginator("list_objects_v2")
            moved = 0
            for page in paginator.paginate(Bucket=_S3_QUARANTINE_BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    expired_key = "expired/" + key.removeprefix("pending/")
                    s3.copy_object(
                        Bucket=_S3_QUARANTINE_BUCKET,
                        CopySource={"Bucket": _S3_QUARANTINE_BUCKET, "Key": key},
                        Key=expired_key,
                        ServerSideEncryption="AES256",
                    )
                    s3.delete_object(Bucket=_S3_QUARANTINE_BUCKET, Key=key)
                    moved += 1
            log.info("Expired %d object(s) under s3://%s/%s", moved, _S3_QUARANTINE_BUCKET, prefix)
            # Zero objects means a previous partial run already moved them.
            expired_table_ids.append(table_id)
        except Exception as exc:  # noqa: BLE001 — keep processing other entries
            log.error("Failed to expire s3://%s/%s: %s", _S3_QUARANTINE_BUCKET, prefix, exc)

    return expired_table_ids


@task
def mark_expired_in_db(table_ids: list[str]) -> None:
    """Update quarantine_manifest.expired_at and write audit log entries."""
    if not table_ids:
        log.info("No expired entries to mark")
        return

    hook = PostgresHook(postgres_conn_id=_DB_CONN)
    for table_id in table_ids:
        hook.run(
            """
            UPDATE quarantine_manifest
            SET expired_at = NOW()
            WHERE table_id = %s AND expired_at IS NULL
            """,
            parameters=(table_id,),
        )
        hook.run(
            """
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES ('quarantine_expired', %s, 'system', %s::jsonb)
            """,
            parameters=(
                table_id,
                f'{{"policy":"{_QUARANTINE_DAYS}d_retention",'
                f'"triggered_by":"dag_quarantine_expiry"}}',
            ),
        )
    log.info("Marked %d entries as expired in quarantine_manifest + audit_log", len(table_ids))


@dag(
    dag_id="quarantine_expiry",
    description="[S8-04] Enforce 30-day quarantine retention policy",
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "platform",
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "email_on_failure": bool(_DPO_EMAIL),
        "email": [_DPO_EMAIL] if _DPO_EMAIL else [],
    },
    tags=["retention", "quarantine", "s8-04"],
)
def quarantine_expiry_dag() -> None:
    expiring = find_expiring_soon()
    warned_ids = send_expiry_warnings(expiring)
    mark_warnings_sent(warned_ids)

    expired = find_expired()
    moved_ids = move_to_expired_prefix(expired)
    mark_expired_in_db(moved_ids)


quarantine_expiry_dag()
