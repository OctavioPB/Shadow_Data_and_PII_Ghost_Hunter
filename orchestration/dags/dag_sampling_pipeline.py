"""
Sampling Pipeline DAG — triggered by patrol_new_tables for each queued event.

Conf keys expected (set by patrol DAG):
  id              — scanner_event UUID
  event_id        — business event_id (for run_id dedup)
  source_name     — fully-qualified table or S3 URI
  data_source_type — athena | glue | s3_parquet | s3_csv | s3_json
  column_count    — estimated number of columns (may be None)

Flow:
  update_status(sampling)
    → run_sampling
    → [persist_column_samples, write_audit_log]
    → update_status(sampled)
    → run_classification          (PIIClassifierOperator)
    → route_result                (@task.branch)
    → trigger_remediation         (high-confidence path)
    | create_manual_review_record (low-confidence path)

Privacy guarantee: samplers never write to source tables/buckets.
All sample output goes to S3_STAGING_BUCKET/samples/{table_id}/.
Values are never persisted to the DB or written to logs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook
from operators.pii_classifier_operator import PIIClassifierOperator
from samplers import get_sampler

log = logging.getLogger(__name__)

_DB_CONN = "pii_hunter_db"
_STAGING_BUCKET = os.environ.get("S3_STAGING_BUCKET", "pii-hunter-staging")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
# data_source_type → Spark reader used by the anonymizer
_SOURCE_FORMATS = {"s3_parquet": "parquet", "s3_csv": "csv", "s3_json": "json"}


@dag(
    dag_id="sampling_pipeline",
    schedule=None,  # triggered externally by patrol_new_tables
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=20,
    tags=["sampling", "pii"],
    default_args={
        "retries": 1,
        "retry_delay": timedelta(minutes=10),
        "owner": "pii-ghost-hunter",
    },
    on_success_callback=lambda ctx: _push_dag_metric(ctx, "success"),
    on_failure_callback=lambda ctx: _push_dag_metric(ctx, "failure"),
    doc_md=__doc__,
)
def sampling_pipeline() -> None:

    # Task parameters are named `run_conf`, never `conf`: `conf` is an Airflow
    # context key, so TaskFlow gives it a default of None and any following
    # positional parameter makes the DAG fail to import ("non-default argument
    # follows default argument").
    @task
    def load_conf(**context) -> dict:
        """Read the trigger conf from the patrol DAG run."""
        run_conf: dict = context["dag_run"].conf or {}
        required = {"id", "source_name", "data_source_type"}
        missing = required - run_conf.keys()
        if missing:
            raise ValueError(f"Missing required conf keys: {missing}")
        return run_conf

    @task
    def update_status_to_sampling(run_conf: dict) -> None:
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        hook.run(
            "UPDATE scanner_events SET status = 'sampling', updated_at = now() WHERE id = %s",
            parameters=[run_conf["id"]],
        )

    @task
    def run_sampling(run_conf: dict) -> dict:
        """
        Route to the correct sampler and execute sampling.
        Returns a JSON-serializable representation of SampleResult.
        Privacy: only column names, dtypes, counts, and S3 path are returned —
        never individual sample values.
        """
        start = time.monotonic()
        sampler = get_sampler(
            data_source_type=run_conf["data_source_type"],
            staging_bucket=_STAGING_BUCKET,
            athena_output_location=f"s3://{_STAGING_BUCKET}/athena-results/",
            aws_region=_AWS_REGION,
        )
        result = sampler.sample(
            source_name=run_conf["source_name"],
            table_id=run_conf["id"],
            # S3Sampler accepts both "parquet" and "s3_parquet" style keys.
            file_format=run_conf["data_source_type"],
        )
        duration_s = time.monotonic() - start
        log.info(
            "Sampling complete: source=%s rows=%d cols=%d duration=%.2fs",
            run_conf["source_name"],
            result.row_count,
            len(result.columns),
            duration_s,
        )
        _push_sampling_duration(run_conf["data_source_type"], duration_s)

        return {
            "source_name": result.source_name,
            "table_id": result.table_id,
            "sample_s3_path": result.sample_s3_path,
            "row_count": result.row_count,
            "columns": [
                {"name": c.name, "dtype": c.dtype, "sample_count": c.sample_count}
                for c in result.columns
            ],
        }

    @task
    def persist_column_samples(run_conf: dict, sampling_result: dict) -> None:
        """Insert one column_samples row per discovered column."""
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        for col in sampling_result["columns"]:
            # Re-sampling refreshes the metadata instead of keeping stale rows.
            hook.run(
                """
                INSERT INTO column_samples
                    (scanner_event_id, table_id, column_name, column_dtype,
                     sample_count, sample_s3_path, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'sampled')
                ON CONFLICT (scanner_event_id, column_name) DO UPDATE SET
                    column_dtype   = EXCLUDED.column_dtype,
                    sample_count   = EXCLUDED.sample_count,
                    sample_s3_path = EXCLUDED.sample_s3_path
                """,
                parameters=[
                    run_conf["id"],
                    sampling_result["table_id"],
                    col["name"],
                    col["dtype"],
                    col["sample_count"],
                    sampling_result["sample_s3_path"],
                ],
            )

    @task
    def write_audit_log(run_conf: dict, sampling_result: dict) -> None:
        """Append-only audit record — never updates or deletes."""
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        hook.run(
            """
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES (%s, %s, %s, %s)
            """,
            parameters=[
                "sampling_completed",
                sampling_result["table_id"],
                "airflow:sampling_pipeline",
                json.dumps(
                    {
                        "source_name": sampling_result["source_name"],
                        "row_count": sampling_result["row_count"],
                        "column_count": len(sampling_result["columns"]),
                        "sample_s3_path": sampling_result["sample_s3_path"],
                        "dag_run_id": run_conf.get("event_id", ""),
                    }
                ),
            ],
        )

    @task
    def update_status_to_sampled(run_conf: dict) -> None:
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        hook.run(
            "UPDATE scanner_events SET status = 'sampled', updated_at = now() WHERE id = %s",
            parameters=[run_conf["id"]],
        )

    @task
    def run_classification(run_conf: dict, sampling_result: dict) -> dict:
        """
        Call PIIClassifierOperator inline as a TaskFlow task.
        Returns a classification summary (no raw values in XCom).
        """
        op = PIIClassifierOperator(
            task_id="run_classification_op",
            scanner_event_id=run_conf["id"],
            table_id=sampling_result["table_id"],
            sample_s3_path=sampling_result["sample_s3_path"],
            columns=sampling_result["columns"],
        )
        return op.execute(context={})

    @task.branch
    def route_result(classification_summary: dict) -> str:
        """Branch: high-confidence PII → remediation; else → manual review."""
        if classification_summary.get("flagged_count", 0) > 0:
            log.info(
                "High-confidence PII found (%d columns) — triggering remediation",
                classification_summary["flagged_count"],
            )
            return "trigger_remediation"
        log.info("No high-confidence PII — creating manual review record")
        return "create_manual_review_record"

    @task(task_id="trigger_remediation")
    def trigger_remediation(run_conf: dict, classification_summary: dict) -> None:
        from airflow.api.common.trigger_dag import trigger_dag as _trigger
        from airflow.exceptions import DagRunAlreadyExists
        from airflow.utils import timezone

        # The remediation DAG skips anonymization AND quarantine when
        # source_s3_path / flagged_columns are missing, so they are resolved
        # here. Only S3 objects of a known format are remediated automatically:
        # a Glue/Athena table's storage format is unknown here, and moving its
        # files out from under the catalog would break the table, so those only
        # notify the DPO.
        source_format = _SOURCE_FORMATS.get(run_conf["data_source_type"])
        source_name = run_conf["source_name"]
        source_s3_path = source_name if source_format and source_name.startswith("s3://") else ""
        if not source_s3_path:
            log.warning(
                "%s is not an S3 object of a known format — remediation will only notify",
                source_name,
            )

        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        row = hook.get_first(
            "SELECT owner_email FROM scanner_events WHERE id = %s",
            parameters=[run_conf["id"]],
        )
        owner_email = row[0] if row else None

        run_id = f"remediation__{run_conf['event_id']}"
        try:
            _trigger(
                dag_id="remediation",
                run_id=run_id,
                conf={
                    "table_id": run_conf["id"],
                    "scanner_event_id": run_conf["id"],
                    "source_name": run_conf["source_name"],
                    "source_s3_path": source_s3_path,
                    "source_format": source_format or "parquet",
                    "owner_email": owner_email,
                    "flagged_categories": classification_summary.get("flagged_categories", []),
                    "flagged_columns": classification_summary.get("flagged_columns", []),
                },
                execution_date=timezone.utcnow(),
                replace_microseconds=False,
            )
        except DagRunAlreadyExists:
            # Task retry after a successful trigger: the run_id is deterministic.
            log.info("Remediation run %s already exists — nothing to do", run_id)
            return
        log.info("Triggered remediation DAG run_id=%s", run_id)

    @task(task_id="create_manual_review_record")
    def create_manual_review_record(run_conf: dict, classification_summary: dict) -> None:
        """Insert a manual_review audit record for the DPO to triage."""
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        hook.run(
            """
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES (%s, %s, %s, %s)
            """,
            parameters=[
                "manual_review_requested",
                run_conf["id"],
                "airflow:sampling_pipeline",
                json.dumps(
                    {
                        "source_name": run_conf["source_name"],
                        "reason": "No high-confidence PII detected — DPO review requested",
                        "total_columns": classification_summary.get("total_count", 0),
                        "flagged_columns": classification_summary.get("flagged_count", 0),
                    }
                ),
            ],
        )

    run_conf = load_conf()
    sampling = update_status_to_sampling(run_conf)
    result = run_sampling(run_conf)
    sampling >> result
    persisted = persist_column_samples(run_conf, result)
    audited = write_audit_log(run_conf, result)
    sampled = update_status_to_sampled(run_conf)
    # 'sampled' only after the samples exist; classification updates
    # column_samples rows, so they must be persisted first.
    [persisted, audited] >> sampled

    # Classification phase
    classification = run_classification(run_conf, result)
    classification.set_upstream(sampled)

    branch = route_result(classification)
    trigger_remediation(run_conf, classification) << branch
    create_manual_review_record(run_conf, classification) << branch


def _push_dag_metric(context: dict, status: str) -> None:
    try:
        from callbacks.dag_prometheus_callback import push_dag_run_metric

        push_dag_run_metric(dag_id=context["dag"].dag_id, status=status)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not push Prometheus metric: %s", exc)


def _push_sampling_duration(data_source_type: str, duration_s: float) -> None:
    try:
        from callbacks.dag_prometheus_callback import push_sampling_duration

        push_sampling_duration(data_source_type=data_source_type, duration_s=duration_s)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not push sampling duration metric: %s", exc)


sampling_dag = sampling_pipeline()
