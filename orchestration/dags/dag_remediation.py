"""
Remediation DAG — S5-05: end-to-end remediation with full audit trail.

Conf keys expected:
  table_id            — scanner_event UUID / logical table ID
  scanner_event_id    — scanner_event UUID
  source_name         — fully-qualified table or S3 URI
  source_s3_path      — S3 URI of the raw data (staging bucket)
  flagged_categories  — list of PII category strings
  flagged_columns     — list of {column_name, pii_category} dicts
  owner_email         — optional: data owner email for the notification

Flow (S5-04: every step is idempotent — safe to re-run after failure):
  load_conf
    → [anonymize_flagged_columns, quarantine_raw_data]  (parallel)
    → notify_dpo
    → write_remediation_audit_log

Privacy:
  - Raw data values are NEVER logged or stored in XCom.
  - Audit log entries contain only metadata (category names, counts, paths).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook

log = logging.getLogger(__name__)

_DB_CONN = "pii_hunter_db"
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_DPO_EMAIL = os.environ.get("DPO_EMAIL", "")
_SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
_SMTP_HOST = os.environ.get("SMTP_HOST", "")
_SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
_DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5173")
_QUARANTINE_BUCKET = os.environ.get("S3_QUARANTINE_BUCKET", "pii-quarantine")


@dag(
    dag_id="remediation",
    schedule=None,  # triggered externally by sampling_pipeline
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=10,
    tags=["remediation", "pii"],
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        "owner": "pii-ghost-hunter",
    },
    doc_md=__doc__,
)
def remediation() -> None:

    @task
    def load_conf(**context) -> dict:
        conf: dict = context["dag_run"].conf or {}
        required = {"table_id", "scanner_event_id", "source_name"}
        missing = required - conf.keys()
        if missing:
            raise ValueError(f"Missing required conf keys: {missing}")
        return conf

    @task
    def anonymize_flagged_columns(conf: dict) -> dict:
        """
        Run PySpark anonymization job on the flagged columns.
        Idempotent: skips if anonymization already completed for this table_id.
        """
        from etl.anonymizers.spark_anonymizer import run_spark_anonymization

        source_path = conf.get("source_s3_path", "")
        flagged_columns = conf.get("flagged_columns", [])

        if not source_path:
            log.warning("No source_s3_path in conf — skipping anonymization")
            return {"skipped": True, "reason": "no_source_path"}

        if not flagged_columns:
            log.info("No flagged columns — skipping anonymization")
            return {"skipped": True, "reason": "no_flagged_columns"}

        result = run_spark_anonymization(
            table_id=conf["table_id"],
            source_path=source_path,
            output_path=source_path,  # in-place overwrite
            flagged_columns=flagged_columns,
            database_url=_DATABASE_URL,
        )
        log.info(
            "Anonymization result: table_id=%s skipped=%s",
            conf["table_id"],
            result.get("skipped"),
        )
        return result

    @task
    def quarantine_raw_data(conf: dict) -> dict:
        """
        Move raw data to the quarantine bucket and write a manifest record.
        Idempotent: skips if quarantine_manifest already exists for this table_id.
        """
        from etl.quarantine.quarantine_job import run_quarantine

        source_path = conf.get("source_s3_path", "")
        if not source_path:
            log.warning("No source_s3_path — skipping quarantine")
            return {"skipped": True, "reason": "no_source_path"}

        result = run_quarantine(
            table_id=conf["table_id"],
            source_s3_path=source_path,
            flagged_categories=conf.get("flagged_categories", []),
            database_url=_DATABASE_URL,
            quarantine_bucket=_QUARANTINE_BUCKET,
        )
        log.info(
            "Quarantine result: table_id=%s files=%s skipped=%s",
            conf["table_id"],
            result.get("file_count"),
            result.get("skipped"),
        )
        return result

    @task
    def notify_dpo(conf: dict, anon_result: dict, quarantine_result: dict) -> dict:
        """
        Send email + Slack to DPO. Retried up to 3× per channel.
        Privacy: only metadata (table_id, categories) included — no raw values.
        """
        from etl.notifiers.dpo_notifier import NotificationContext, send_dpo_notifications

        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        rows = hook.get_records(
            """
            SELECT column_name, pii_category, confidence
            FROM pii_findings
            WHERE table_id = %s AND flagged = true
            ORDER BY confidence DESC
            LIMIT 20
            """,
            parameters=[conf["table_id"]],
        )
        findings = [
            {"column_name": r[0], "pii_category": r[1], "confidence": r[2]} for r in rows
        ]
        max_confidence = max((f["confidence"] for f in findings), default=0.0)

        ctx = NotificationContext(
            table_id=conf["table_id"],
            source_name=conf["source_name"],
            flagged_categories=conf.get("flagged_categories", []),
            max_confidence=max_confidence,
            owner_email=conf.get("owner_email"),
            findings=findings,
            dashboard_url=_DASHBOARD_URL,
            detected_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        )

        results = send_dpo_notifications(
            ctx=ctx,
            database_url=_DATABASE_URL,
            dpo_email=_DPO_EMAIL,
            slack_webhook_url=_SLACK_WEBHOOK_URL,
            smtp_host=_SMTP_HOST,
            smtp_port=_SMTP_PORT,
        )
        return results

    @task
    def write_remediation_audit_log(
        conf: dict,
        anon_result: dict,
        quarantine_result: dict,
        notification_result: dict,
    ) -> None:
        """Append-only audit record for the full remediation run — S5-05."""
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        hook.run(
            """
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES (%s, %s, %s, %s)
            """,
            parameters=[
                "remediation_completed",
                conf["table_id"],
                "airflow:remediation",
                json.dumps(
                    {
                        "source_name": conf["source_name"],
                        "flagged_categories": conf.get("flagged_categories", []),
                        "anonymization": {
                            "skipped": anon_result.get("skipped", False),
                            "row_count": anon_result.get("row_count"),
                            "columns_anonymized": anon_result.get("anonymized_columns", []),
                        },
                        "quarantine": {
                            "skipped": quarantine_result.get("skipped", False),
                            "manifest_id": quarantine_result.get("manifest_id"),
                            "file_count": quarantine_result.get("file_count"),
                        },
                        "notifications": {
                            "email": notification_result.get("email", {}).get("status"),
                            "slack": notification_result.get("slack", {}).get("status"),
                        },
                    }
                ),
            ],
        )
        log.info("Remediation audit log written for table_id=%s", conf["table_id"])

    # ── DAG wiring ────────────────────────────────────────────────────────────
    conf = load_conf()

    # Anonymization and quarantine run in parallel
    anon = anonymize_flagged_columns(conf)
    quarantine = quarantine_raw_data(conf)

    # Notification waits for both
    notification = notify_dpo(conf, anon, quarantine)

    # Audit log is written last
    write_remediation_audit_log(conf, anon, quarantine, notification)


remediation_dag = remediation()
