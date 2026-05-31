"""
Patrol DAG — scans newly created tables every 24 hours.

Schedule: @daily
Idempotency: uses data_interval_start/end as the query window;
             status CAS (pending → queued) prevents double-enqueue on re-runs.

Flow:
  fetch_pending_events → enqueue_for_sampling → trigger_sampling_runs
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from airflow.decorators import dag, task
from airflow.providers.postgres.hooks.postgres import PostgresHook

log = logging.getLogger(__name__)

_DB_CONN = "pii_hunter_db"
_SAMPLING_DAG_ID = "sampling_pipeline"


@dag(
    dag_id="patrol_new_tables",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["patrol", "pii"],
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "owner": "pii-ghost-hunter",
    },
    on_success_callback=lambda ctx: _push_dag_metric(ctx, "success"),
    on_failure_callback=lambda ctx: _push_dag_metric(ctx, "failure"),
    doc_md=__doc__,
)
def patrol_new_tables() -> None:

    @task
    def fetch_pending_events(
        data_interval_start: datetime | None = None,
        data_interval_end: datetime | None = None,
    ) -> list[dict]:
        """
        Query scanner_events with status='pending' inside the 24-h window.
        Uses parameterized queries — no string interpolation.
        """
        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        rows = hook.get_records(
            """
            SELECT id, event_id, source_name, data_source_type, column_count
            FROM scanner_events
            WHERE status = 'pending'
              AND created_at >= %s
              AND created_at < %s
            ORDER BY created_at
            """,
            parameters=[data_interval_start, data_interval_end],
        )
        events = [
            {
                "id": str(row[0]),
                "event_id": row[1],
                "source_name": row[2],
                "data_source_type": row[3],
                "column_count": row[4],
            }
            for row in rows
        ]
        log.info("Patrol window %s→%s: %d pending events", data_interval_start, data_interval_end, len(events))
        return events

    @task
    def enqueue_for_sampling(events: list[dict]) -> list[dict]:
        """
        CAS update: pending → queued.  Re-runs are safe because the WHERE
        clause includes `AND status = 'pending'`, so already-queued rows are skipped.
        """
        if not events:
            log.info("No pending events to enqueue")
            return []

        hook = PostgresHook(postgres_conn_id=_DB_CONN)
        queued: list[dict] = []
        for event in events:
            hook.run(
                """
                UPDATE scanner_events
                SET status = 'queued', updated_at = now()
                WHERE id = %s AND status = 'pending'
                """,
                parameters=[event["id"]],
            )
            queued.append(event)
            log.info("Enqueued event_id=%s source=%s", event["event_id"], event["source_name"])

        log.info("Enqueued %d events for sampling", len(queued))
        return queued

    @task
    def trigger_sampling_runs(queued_events: list[dict]) -> None:
        """Trigger one sampling_pipeline DAG run per enqueued event."""
        if not queued_events:
            return

        from airflow.api.common.trigger_dag import trigger_dag as _trigger
        from airflow.utils import timezone

        for event in queued_events:
            run_id = f"patrol__{event['event_id']}"
            _trigger(
                dag_id=_SAMPLING_DAG_ID,
                run_id=run_id,
                conf=event,
                execution_date=timezone.utcnow(),
                replace_microseconds=False,
            )
            log.info("Triggered %s run_id=%s", _SAMPLING_DAG_ID, run_id)

    events = fetch_pending_events()
    queued = enqueue_for_sampling(events)
    trigger_sampling_runs(queued)


def _push_dag_metric(context: dict, status: str) -> None:
    """Prometheus callback — imported lazily to avoid import-time side-effects."""
    try:
        from callbacks.dag_prometheus_callback import push_dag_run_metric

        push_dag_run_metric(dag_id=context["dag"].dag_id, status=status)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not push Prometheus metric: %s", exc)


patrol_dag = patrol_new_tables()
