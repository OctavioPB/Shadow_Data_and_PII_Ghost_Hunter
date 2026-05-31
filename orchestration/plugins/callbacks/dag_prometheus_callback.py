"""
Airflow → Prometheus bridge using the Pushgateway textfile approach.

Each DAG declares:
    on_success_callback=lambda ctx: _push_dag_metric(ctx, "success")
    on_failure_callback=lambda ctx: _push_dag_metric(ctx, "failure")

Metrics pushed:
  airflow_dag_runs_total{dag_id, status}          — counter
  airflow_sampling_duration_seconds{source_type}  — histogram observation
"""

import logging
import os

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    push_to_gateway,
)

log = logging.getLogger(__name__)

_PUSHGATEWAY = os.environ.get("PROMETHEUS_PUSHGATEWAY_URL", "http://localhost:9091")
_JOB = "airflow-pii-ghost-hunter"

_SAMPLING_BUCKETS = (5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0)


def push_dag_run_metric(dag_id: str, status: str) -> None:
    """Increment the dag_runs counter for the given dag_id and status."""
    registry = CollectorRegistry()
    counter = Counter(
        "airflow_dag_runs_total",
        "Total Airflow DAG run completions by status",
        ["dag_id", "status"],
        registry=registry,
    )
    counter.labels(dag_id=dag_id, status=status).inc()
    _push(registry, grouping_key={"dag_id": dag_id})


def push_sampling_duration(data_source_type: str, duration_s: float) -> None:
    """Record sampling duration for a given data source type."""
    registry = CollectorRegistry()
    hist = Histogram(
        "airflow_sampling_duration_seconds",
        "Time in seconds to sample a single event",
        ["data_source_type"],
        buckets=_SAMPLING_BUCKETS,
        registry=registry,
    )
    hist.labels(data_source_type=data_source_type).observe(duration_s)
    _push(registry, grouping_key={"data_source_type": data_source_type})


def _push(registry: CollectorRegistry, grouping_key: dict) -> None:
    try:
        push_to_gateway(_PUSHGATEWAY, job=_JOB, registry=registry, grouping_key=grouping_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("Pushgateway unavailable (%s) — metric dropped", exc)
