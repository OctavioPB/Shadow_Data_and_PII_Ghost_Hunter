"""
Remediation DAG — S5-05: end-to-end remediation with full audit trail.

Conf keys expected:
  table_id            — scanner_event UUID / logical table ID
  scanner_event_id    — scanner_event UUID
  source_name         — fully-qualified table or S3 URI
  source_s3_path      — S3 URI of the raw data
  source_format       — parquet | csv | json (default parquet)
  flagged_categories  — list of PII category strings
  flagged_columns     — list of {column_name, pii_category} dicts
  owner_email         — optional: data owner email for the notification

Flow (S5-04: every step is idempotent — safe to re-run after failure):
  load_conf
    → anonymize_flagged_columns   (raw source → <source>__anonymized_staging/)
    → quarantine_raw_data         (raw source → quarantine bucket, source deleted)
    → promote_anonymized_output   (staging → original source location)
    → notify_dpo
    → write_remediation_audit_log

The steps are strictly sequential. They used to run anonymization (in-place
overwrite of source_s3_path) and quarantine (copy + DELETE of the same path) in
parallel, a race that could quarantine already-anonymized files, delete Spark's
output, or make Spark read half-deleted input.

Failure handling:
  - quarantine runs even if anonymization failed (trigger_rule=all_done), as it
    did before: containing raw PII must not depend on Spark succeeding.
  - promotion only runs when both succeeded, so partial Spark output is never
    copied over the source location.
  - notification and the audit record always run, so a failed remediation is
    still reported to the DPO.

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
from airflow.operators.python import get_current_context
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
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_STAGING_SUFFIX = "__anonymized_staging"


def _staging_path(source_path: str) -> str:
    """Sibling prefix for the anonymized output (never inside the source prefix)."""
    return source_path.rstrip("/") + _STAGING_SUFFIX + "/"


def _upstream_result(task_id: str) -> dict:
    """XCom of an upstream task, or a failure marker if it did not succeed.

    Tasks with trigger_rule=all_done cannot take failed tasks' outputs as
    TaskFlow arguments (resolving a missing XCom raises), so they pull them.
    """
    ti = get_current_context()["ti"]
    value = ti.xcom_pull(task_ids=task_id)
    return value if isinstance(value, dict) else {"failed": True}


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

    # Task parameters are named `run_conf`, never `conf`: `conf` is an Airflow
    # context key, so TaskFlow gives it a default of None and any following
    # positional parameter makes the DAG fail to import ("non-default argument
    # follows default argument").
    @task
    def load_conf(**context) -> dict:
        run_conf: dict = context["dag_run"].conf or {}
        required = {"table_id", "scanner_event_id", "source_name"}
        missing = required - run_conf.keys()
        if missing:
            raise ValueError(f"Missing required conf keys: {missing}")
        return run_conf

    @task
    def anonymize_flagged_columns(run_conf: dict) -> dict:
        """
        Run PySpark anonymization job on the flagged columns.
        Idempotent: skips if anonymization already completed for this table_id.
        """
        from etl.anonymizers.spark_anonymizer import run_spark_anonymization

        source_path = run_conf.get("source_s3_path", "")
        flagged_columns = run_conf.get("flagged_columns", [])

        if not source_path:
            log.warning("No source_s3_path in conf — skipping anonymization")
            return {"skipped": True, "reason": "no_source_path"}

        if not flagged_columns:
            log.info("No flagged columns — skipping anonymization")
            return {"skipped": True, "reason": "no_flagged_columns"}

        result = run_spark_anonymization(
            table_id=run_conf["table_id"],
            source_path=source_path,
            output_path=_staging_path(source_path),
            flagged_columns=flagged_columns,
            database_url=_DATABASE_URL,
            source_format=run_conf.get("source_format", "parquet"),
        )
        log.info(
            "Anonymization result: table_id=%s skipped=%s",
            run_conf["table_id"],
            result.get("skipped"),
        )
        return result

    @task(trigger_rule="all_done")
    def quarantine_raw_data(run_conf: dict) -> dict:
        """
        Move raw data to the quarantine bucket and write a manifest record.
        Idempotent: skips if quarantine_manifest already exists for this table_id.
        """
        from etl.quarantine.quarantine_job import run_quarantine

        source_path = run_conf.get("source_s3_path", "")
        if not source_path:
            log.warning("No source_s3_path — skipping quarantine")
            return {"skipped": True, "reason": "no_source_path"}

        result = run_quarantine(
            table_id=run_conf["table_id"],
            source_s3_path=source_path,
            flagged_categories=run_conf.get("flagged_categories", []),
            database_url=_DATABASE_URL,
            quarantine_bucket=_QUARANTINE_BUCKET,
        )
        log.info(
            "Quarantine result: table_id=%s files=%s skipped=%s",
            run_conf["table_id"],
            result.get("file_count"),
            result.get("skipped"),
        )
        return result

    @task
    def promote_anonymized_output(run_conf: dict, anon_result: dict, quarantine_result: dict) -> dict:
        """
        Move the anonymized staging output into the original location, now that
        the raw data has been moved to quarantine. Idempotent: an empty staging
        prefix (already promoted, or nothing anonymized) is a no-op.
        """
        import boto3

        from etl.quarantine.quarantine_job import _move_s3_prefix

        source_path = run_conf.get("source_s3_path", "")
        if not source_path or not source_path.startswith("s3://"):
            return {"skipped": True, "reason": "no_source_path"}

        staging = _staging_path(source_path)
        bucket, _, staging_prefix = staging[len("s3://"):].partition("/")
        _, _, dest_prefix = source_path[len("s3://"):].partition("/")

        s3 = boto3.client("s3", region_name=_AWS_REGION)
        file_count, total_bytes = _move_s3_prefix(
            s3, bucket, staging_prefix, bucket, dest_prefix.rstrip("/") + "/"
        )
        log.info(
            "Promoted anonymized output: table_id=%s files=%d", run_conf["table_id"], file_count
        )
        return {"skipped": file_count == 0, "file_count": file_count, "total_bytes": total_bytes}

    @task(trigger_rule="all_done")
    def notify_dpo(run_conf: dict) -> dict:
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
            parameters=[run_conf["table_id"]],
        )
        findings = [
            {"column_name": r[0], "pii_category": r[1], "confidence": r[2]} for r in rows
        ]
        max_confidence = max((f["confidence"] for f in findings), default=0.0)

        ctx = NotificationContext(
            table_id=run_conf["table_id"],
            source_name=run_conf["source_name"],
            flagged_categories=run_conf.get("flagged_categories", []),
            max_confidence=max_confidence,
            owner_email=run_conf.get("owner_email"),
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

    @task(trigger_rule="all_done")
    def write_remediation_audit_log(run_conf: dict) -> None:
        """Append-only audit record for the full remediation run — S5-05."""
        anon_result = _upstream_result("anonymize_flagged_columns")
        quarantine_result = _upstream_result("quarantine_raw_data")
        promotion_result = _upstream_result("promote_anonymized_output")
        notification_result = _upstream_result("notify_dpo")
        failed = any(
            r.get("failed")
            for r in (anon_result, quarantine_result, promotion_result, notification_result)
        )
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        hook.run(
            """
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES (%s, %s, %s, %s)
            """,
            parameters=[
                "remediation_failed" if failed else "remediation_completed",
                run_conf["table_id"],
                "airflow:remediation",
                json.dumps(
                    {
                        "source_name": run_conf["source_name"],
                        "flagged_categories": run_conf.get("flagged_categories", []),
                        "anonymization": {
                            "failed": anon_result.get("failed", False),
                            "skipped": anon_result.get("skipped", False),
                            "row_count": anon_result.get("row_count"),
                            "columns_anonymized": anon_result.get("anonymized_columns", []),
                        },
                        "quarantine": {
                            "failed": quarantine_result.get("failed", False),
                            "skipped": quarantine_result.get("skipped", False),
                            "manifest_id": quarantine_result.get("manifest_id"),
                            "file_count": quarantine_result.get("file_count"),
                        },
                        "promotion": {
                            "failed": promotion_result.get("failed", False),
                            "file_count": promotion_result.get("file_count"),
                        },
                        # A channel that is not configured is stored as None, so
                        # `.get("email", {})` returned None and `.get` crashed.
                        "notifications": {
                            "email": (notification_result.get("email") or {}).get("status"),
                            "slack": (notification_result.get("slack") or {}).get("status"),
                        },
                    }
                ),
            ],
        )
        log.info("Remediation audit log written for table_id=%s", run_conf["table_id"])

    # ── DAG wiring ────────────────────────────────────────────────────────────
    run_conf = load_conf()

    # Strictly sequential: both jobs touch source_s3_path (see module docstring).
    anon = anonymize_flagged_columns(run_conf)
    quarantine = quarantine_raw_data(run_conf)
    anon >> quarantine
    promoted = promote_anonymized_output(run_conf, anon, quarantine)

    notification = notify_dpo(run_conf)
    promoted >> notification

    # Audit log is written last
    audit = write_remediation_audit_log(run_conf)
    notification >> audit


remediation_dag = remediation()
