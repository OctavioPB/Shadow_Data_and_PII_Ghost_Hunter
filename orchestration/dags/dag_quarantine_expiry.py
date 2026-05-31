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
and quarantine_manifest.expired_at columns).
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
_SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
_SMTP_HOST = os.environ.get("SMTP_HOST", "")
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_SMTP_USER = os.environ.get("SMTP_USER", "")
_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
_FROM_EMAIL = os.environ.get("NOTIFICATION_FROM_EMAIL", "noreply@company.com")
_S3_QUARANTINE_BUCKET = os.environ.get("S3_QUARANTINE_BUCKET", "pii-quarantine")
_QUARANTINE_DAYS = int(os.environ.get("QUARANTINE_RETENTION_DAYS", "30"))
_WARNING_DAYS_BEFORE = int(os.environ.get("QUARANTINE_WARNING_DAYS_BEFORE", "7"))


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
        "email_on_failure": True,
        "email": [_DPO_EMAIL],
    },
    tags=["retention", "quarantine", "s8-04"],
)
def quarantine_expiry_dag() -> None:

    @task
    def find_expiring_soon() -> list[dict]:
        """
        Returns quarantine records that are >= (QUARANTINE_DAYS - WARNING_DAYS_BEFORE)
        old and have not yet had a warning sent.
        """
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        warning_threshold = _QUARANTINE_DAYS - _WARNING_DAYS_BEFORE
        rows = hook.get_records(
            """
            SELECT
                table_id,
                s3_path,
                quarantined_at,
                owner_email
            FROM quarantine_manifest
            WHERE expired_at IS NULL
              AND warning_sent_at IS NULL
              AND quarantined_at <= NOW() - INTERVAL '%s days'
            ORDER BY quarantined_at ASC
            """,
            parameters=(warning_threshold,),
        )
        log.info("Found %d quarantine entries approaching expiry", len(rows))
        return [
            {
                "table_id": str(r[0]),
                "s3_path": str(r[1]),
                "quarantined_at": r[2].isoformat(),
                "owner_email": str(r[3]) if r[3] else None,
                "days_remaining": _QUARANTINE_DAYS - (
                    datetime.now(tz=timezone.utc) - r[2].replace(tzinfo=timezone.utc)
                ).days,
            }
            for r in rows
        ]

    @task
    def send_expiry_warnings(expiring: list[dict]) -> list[str]:
        """
        Sends DPO warning emails for each expiring quarantine entry.
        Returns table_ids for which warnings were successfully sent.
        """
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        warned_table_ids: list[str] = []

        for entry in expiring:
            table_id = entry["table_id"]
            days_remaining = entry["days_remaining"]
            s3_path = entry["s3_path"]

            subject = f"[PII Ghost-Hunter] Quarantine Expiry Warning: {table_id}"
            body = f"""
Data in quarantine is scheduled for automatic deletion in {days_remaining} day(s).

Table ID:       {table_id}
S3 Path:        {s3_path}
Quarantined at: {entry['quarantined_at']}
Auto-delete at: {entry['quarantined_at'][:10]} + 30 days

ACTION REQUIRED if you wish to retain this data beyond the 30-day window:
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

                if _SMTP_HOST:
                    with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
                        server.starttls()
                        if _SMTP_USER:
                            server.login(_SMTP_USER, _SMTP_PASSWORD)
                        server.sendmail(_FROM_EMAIL, recipients, msg.as_string())

                log.info("Expiry warning sent for table_id=%s to %s", table_id, recipients)
                warned_table_ids.append(table_id)

            except Exception as exc:
                log.error("Failed to send warning for table_id=%s: %s", table_id, exc)

        return warned_table_ids

    @task
    def mark_warnings_sent(table_ids: list[str]) -> None:
        """Update quarantine_manifest.warning_sent_at for successfully warned entries."""
        if not table_ids:
            log.info("No warnings to mark")
            return

        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        for table_id in table_ids:
            hook.run(
                """
                UPDATE quarantine_manifest
                SET warning_sent_at = NOW()
                WHERE table_id = %s
                  AND expired_at IS NULL
                  AND warning_sent_at IS NULL
                """,
                parameters=(table_id,),
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
            """
            SELECT
                table_id,
                s3_path,
                quarantined_at
            FROM quarantine_manifest
            WHERE expired_at IS NULL
              AND quarantined_at <= NOW() - INTERVAL '%s days'
            ORDER BY quarantined_at ASC
            """,
            parameters=(_QUARANTINE_DAYS,),
        )
        log.info("Found %d quarantine entries past retention window", len(rows))
        return [
            {
                "table_id": str(r[0]),
                "s3_path": str(r[1]),
                "quarantined_at": r[2].isoformat(),
            }
            for r in rows
        ]

    @task
    def move_to_expired_prefix(expired: list[dict]) -> list[str]:
        """
        Copies each expired object from pending/ to expired/ prefix and deletes the original.
        The S3 lifecycle rule on the expired/ prefix deletes within 24h.

        Privacy: only S3 key paths are logged — never object contents.
        """
        import boto3

        s3 = boto3.client("s3")
        expired_table_ids: list[str] = []

        for entry in expired:
            table_id = entry["table_id"]
            s3_path = entry["s3_path"]

            if not s3_path.startswith(f"s3://{_S3_QUARANTINE_BUCKET}/"):
                log.error(
                    "Unexpected S3 path for table_id=%s: %s — skipping",
                    table_id,
                    s3_path,
                )
                continue

            key = s3_path.removeprefix(f"s3://{_S3_QUARANTINE_BUCKET}/")
            expired_key = key.replace("pending/", "expired/", 1)

            try:
                s3.copy_object(
                    Bucket=_S3_QUARANTINE_BUCKET,
                    CopySource={"Bucket": _S3_QUARANTINE_BUCKET, "Key": key},
                    Key=expired_key,
                )
                s3.delete_object(Bucket=_S3_QUARANTINE_BUCKET, Key=key)
                log.info("Moved expired object: %s → %s", key, expired_key)
                expired_table_ids.append(table_id)

            except Exception as exc:
                log.error(
                    "Failed to expire s3://%s/%s: %s",
                    _S3_QUARANTINE_BUCKET,
                    key,
                    exc,
                )

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
                VALUES ('quarantine_expired', %s, 'system',
                        '{"policy":"30d_retention","triggered_by":"dag_quarantine_expiry"}'::jsonb)
                """,
                parameters=(table_id,),
            )
        log.info("Marked %d entries as expired in quarantine_manifest + audit_log", len(table_ids))

    # ─── DAG wiring ───────────────────────────────────────────────────────────
    expiring = find_expiring_soon()
    warned_ids = send_expiry_warnings(expiring)
    mark_warnings_sent(warned_ids)

    expired = find_expired()
    moved_ids = move_to_expired_prefix(expired)
    mark_expired_in_db(moved_ids)


quarantine_expiry_dag()
