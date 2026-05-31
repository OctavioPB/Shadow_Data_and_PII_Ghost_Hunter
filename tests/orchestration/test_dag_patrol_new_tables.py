"""
Unit tests for dag_patrol_new_tables.

Airflow is not started — individual task callables are extracted and tested
in isolation using mocked Hooks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_db_rows(n: int) -> list[tuple]:
    """Produce synthetic scanner_events rows as the Postgres Hook would return."""
    import uuid

    return [
        (
            uuid.uuid4(),                    # id
            f"event-{i:04d}",               # event_id
            f"prod.table_{i}",              # source_name
            "glue",                          # data_source_type
            10 + i,                          # column_count
        )
        for i in range(n)
    ]


# ─── fetch_pending_events ─────────────────────────────────────────────────────


def test_fetch_pending_events_returns_correct_shape():
    from orchestration.dags.dag_patrol_new_tables import patrol_new_tables

    dag = patrol_new_tables
    fetch_fn = dag.task_dict["fetch_pending_events"].python_callable

    rows = _make_db_rows(3)
    mock_hook = MagicMock()
    mock_hook.get_records.return_value = rows

    with patch(
        "orchestration.dags.dag_patrol_new_tables.PostgresHook",
        return_value=mock_hook,
    ):
        result = fetch_fn(
            data_interval_start=datetime(2026, 5, 15, tzinfo=timezone.utc),
            data_interval_end=datetime(2026, 5, 16, tzinfo=timezone.utc),
        )

    assert len(result) == 3
    assert result[0]["event_id"] == "event-0000"
    assert result[0]["data_source_type"] == "glue"
    assert isinstance(result[0]["id"], str)  # UUID serialised to string


def test_fetch_pending_events_empty_window():
    from orchestration.dags.dag_patrol_new_tables import patrol_new_tables

    dag = patrol_new_tables
    fetch_fn = dag.task_dict["fetch_pending_events"].python_callable

    mock_hook = MagicMock()
    mock_hook.get_records.return_value = []

    with patch(
        "orchestration.dags.dag_patrol_new_tables.PostgresHook",
        return_value=mock_hook,
    ):
        result = fetch_fn(
            data_interval_start=datetime(2026, 5, 15, tzinfo=timezone.utc),
            data_interval_end=datetime(2026, 5, 16, tzinfo=timezone.utc),
        )

    assert result == []


def test_fetch_pending_events_uses_parameterized_query():
    """The SQL must never use string interpolation for dates."""
    from orchestration.dags.dag_patrol_new_tables import patrol_new_tables

    dag = patrol_new_tables
    fetch_fn = dag.task_dict["fetch_pending_events"].python_callable
    mock_hook = MagicMock()
    mock_hook.get_records.return_value = []

    start = datetime(2026, 5, 15, tzinfo=timezone.utc)
    end = datetime(2026, 5, 16, tzinfo=timezone.utc)

    with patch(
        "orchestration.dags.dag_patrol_new_tables.PostgresHook",
        return_value=mock_hook,
    ):
        fetch_fn(data_interval_start=start, data_interval_end=end)

    _, call_kwargs = mock_hook.get_records.call_args
    params = call_kwargs.get("parameters") or mock_hook.get_records.call_args.args[1]
    assert params == [start, end], "Query must be parameterized with exact datetime values"


# ─── enqueue_for_sampling ────────────────────────────────────────────────────


def test_enqueue_for_sampling_updates_status_with_cas():
    """Only events still pending should be updated (WHERE status='pending')."""
    from orchestration.dags.dag_patrol_new_tables import patrol_new_tables

    dag = patrol_new_tables
    enqueue_fn = dag.task_dict["enqueue_for_sampling"].python_callable

    events = [
        {"id": "uuid-001", "event_id": "evt-001", "source_name": "db.t1", "data_source_type": "glue", "column_count": 5},
        {"id": "uuid-002", "event_id": "evt-002", "source_name": "db.t2", "data_source_type": "athena", "column_count": 8},
    ]
    mock_hook = MagicMock()

    with patch(
        "orchestration.dags.dag_patrol_new_tables.PostgresHook",
        return_value=mock_hook,
    ):
        result = enqueue_fn(events)

    assert len(result) == 2
    assert mock_hook.run.call_count == 2
    # Verify CAS — status='pending' must appear in the SQL
    sql_called = mock_hook.run.call_args_list[0].args[0]
    assert "status = 'pending'" in sql_called


def test_enqueue_for_sampling_empty_list_returns_empty():
    from orchestration.dags.dag_patrol_new_tables import patrol_new_tables

    dag = patrol_new_tables
    enqueue_fn = dag.task_dict["enqueue_for_sampling"].python_callable
    result = enqueue_fn([])
    assert result == []


# ─── trigger_sampling_runs ───────────────────────────────────────────────────


def test_trigger_sampling_runs_one_trigger_per_event():
    from orchestration.dags.dag_patrol_new_tables import patrol_new_tables

    dag = patrol_new_tables
    trigger_fn = dag.task_dict["trigger_sampling_runs"].python_callable

    events = [
        {"id": "u1", "event_id": "e1", "source_name": "db.t1", "data_source_type": "glue", "column_count": 3},
        {"id": "u2", "event_id": "e2", "source_name": "db.t2", "data_source_type": "athena", "column_count": 7},
    ]

    with (
        patch("orchestration.dags.dag_patrol_new_tables.timezone") as mock_tz,
        patch("orchestration.dags.dag_patrol_new_tables._trigger") as mock_trigger,
    ):
        mock_tz.utcnow.return_value = datetime(2026, 5, 16, tzinfo=timezone.utc)

        # Inline the import so we can patch it
        import orchestration.dags.dag_patrol_new_tables as mod

        with patch.object(mod, "_trigger" if hasattr(mod, "_trigger") else "__builtins__", create=True):
            with patch("airflow.api.common.trigger_dag.trigger_dag") as mock_api_trigger:
                with patch("airflow.utils.timezone"):
                    trigger_fn(events)
                    assert mock_api_trigger.call_count == 2
                    run_ids = [c.kwargs["run_id"] for c in mock_api_trigger.call_args_list]
                    assert any("e1" in rid for rid in run_ids)
                    assert any("e2" in rid for rid in run_ids)


def test_trigger_sampling_runs_empty_list_is_noop():
    from orchestration.dags.dag_patrol_new_tables import patrol_new_tables

    dag = patrol_new_tables
    trigger_fn = dag.task_dict["trigger_sampling_runs"].python_callable
    # Must not raise and must call no trigger
    with patch("airflow.api.common.trigger_dag.trigger_dag") as mock_api_trigger:
        trigger_fn([])
        mock_api_trigger.assert_not_called()
