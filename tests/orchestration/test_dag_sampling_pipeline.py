"""
Unit tests for dag_sampling_pipeline.

Tasks are extracted from the DAG object and tested with mocked dependencies.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Airflow 2.8 pins SQLAlchemy<2, so it cannot share an env with the API; these
# tests run in the `airflow-test` CI job (apache/airflow image) instead.
pytest.importorskip("airflow", reason="apache-airflow not installed")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_conf(overrides: dict | None = None) -> dict:
    base = {
        "id": "scanner-event-uuid-001",
        "event_id": "evt-001",
        "source_name": "prod.customer_backup",
        "data_source_type": "glue",
        "column_count": 8,
    }
    return {**base, **(overrides or {})}


def _make_sample_result() -> dict:
    return {
        "source_name": "prod.customer_backup",
        "table_id": "scanner-event-uuid-001",
        "sample_s3_path": "s3://pii-hunter-staging/samples/scanner-event-uuid-001/sample.parquet",
        "row_count": 874,
        "columns": [
            {"name": "id", "dtype": "int64", "sample_count": 874},
            {"name": "email", "dtype": "object", "sample_count": 874},
            {"name": "cpf", "dtype": "object", "sample_count": 850},
        ],
    }


# ─── load_conf ────────────────────────────────────────────────────────────────


def test_load_conf_returns_conf_from_dag_run():
    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    dag = sampling_pipeline
    load_fn = dag.task_dict["load_conf"].python_callable

    conf = _make_conf()
    context = {"dag_run": MagicMock(conf=conf)}
    result = load_fn(**context)
    assert result["id"] == conf["id"]
    assert result["source_name"] == conf["source_name"]


def test_load_conf_raises_when_required_key_missing():
    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    dag = sampling_pipeline
    load_fn = dag.task_dict["load_conf"].python_callable

    context = {"dag_run": MagicMock(conf={"source_name": "db.t"})}  # missing id
    with pytest.raises(ValueError, match="Missing required conf keys"):
        load_fn(**context)


# ─── run_sampling ─────────────────────────────────────────────────────────────


def test_run_sampling_routes_to_correct_sampler():
    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    dag = sampling_pipeline
    run_fn = dag.task_dict["run_sampling"].python_callable

    conf = _make_conf()
    mock_sampler = MagicMock()
    mock_result = MagicMock()
    mock_result.source_name = conf["source_name"]
    mock_result.table_id = conf["id"]
    mock_result.row_count = 500
    mock_result.sample_s3_path = "s3://staging/samples/t/sample.parquet"
    mock_result.columns = []
    mock_sampler.sample.return_value = mock_result

    with patch("orchestration.dags.dag_sampling_pipeline.get_sampler", return_value=mock_sampler):
        result = run_fn(conf)

    mock_sampler.sample.assert_called_once_with(
        source_name=conf["source_name"],
        table_id=conf["id"],
        file_format=conf["data_source_type"],
    )
    assert result["row_count"] == 500


def test_run_sampling_result_contains_no_raw_values():
    """Privacy: output must contain only metadata, never individual cell values."""
    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    dag = sampling_pipeline
    run_fn = dag.task_dict["run_sampling"].python_callable

    conf = _make_conf()
    mock_sampler = MagicMock()
    col = MagicMock(name="email", dtype="object", sample_count=874)
    col.name = "email"
    col.dtype = "object"
    col.sample_count = 874
    mock_result = MagicMock(
        source_name=conf["source_name"],
        table_id=conf["id"],
        row_count=874,
        sample_s3_path="s3://staging/s/sample.parquet",
        columns=[col],
    )
    mock_sampler.sample.return_value = mock_result

    with patch("orchestration.dags.dag_sampling_pipeline.get_sampler", return_value=mock_sampler):
        result = run_fn(conf)

    # result["columns"] must be a list of dicts with only name/dtype/sample_count
    assert isinstance(result["columns"], list)
    for c in result["columns"]:
        assert set(c.keys()) == {"name", "dtype", "sample_count"}, (
            "Column metadata must not include raw sample values"
        )


# ─── persist_column_samples ──────────────────────────────────────────────────


def test_persist_column_samples_inserts_one_row_per_column():
    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    dag = sampling_pipeline
    persist_fn = dag.task_dict["persist_column_samples"].python_callable

    conf = _make_conf()
    result = _make_sample_result()

    mock_hook = MagicMock()
    with patch(
        "orchestration.dags.dag_sampling_pipeline.PostgresHook",
        return_value=mock_hook,
    ):
        persist_fn(conf, result)

    assert mock_hook.run.call_count == 3  # 3 columns in result


# ─── write_audit_log ─────────────────────────────────────────────────────────


def test_write_audit_log_uses_insert_only():
    """audit_log task must only INSERT, never UPDATE or DELETE."""
    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    dag = sampling_pipeline
    audit_fn = dag.task_dict["write_audit_log"].python_callable

    mock_hook = MagicMock()
    with patch(
        "orchestration.dags.dag_sampling_pipeline.PostgresHook",
        return_value=mock_hook,
    ):
        audit_fn(_make_conf(), _make_sample_result())

    sql: str = mock_hook.run.call_args.args[0]
    assert sql.strip().upper().startswith("INSERT"), "Audit log must use INSERT only"
    assert "UPDATE" not in sql.upper()
    assert "DELETE" not in sql.upper()


def test_write_audit_log_does_not_include_pii_values():
    """Audit log details must not contain raw sample values — only metadata."""
    import json as _json

    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    dag = sampling_pipeline
    audit_fn = dag.task_dict["write_audit_log"].python_callable

    mock_hook = MagicMock()
    with patch(
        "orchestration.dags.dag_sampling_pipeline.PostgresHook",
        return_value=mock_hook,
    ):
        audit_fn(_make_conf(), _make_sample_result())

    params = mock_hook.run.call_args.kwargs["parameters"]
    details = _json.loads(params[3])

    assert "source_name" in details
    assert "row_count" in details
    assert "column_count" in details
    # No raw cell values should appear in the details
    assert "Alice" not in str(details)
    assert "a@b.com" not in str(details)


# ─── trigger_remediation ─────────────────────────────────────────────────────


def test_trigger_remediation_passes_what_remediation_needs():
    """Without source_s3_path/flagged_columns the remediation DAG skips both
    anonymization and quarantine, so they must be in the trigger conf."""
    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    trigger_fn = sampling_pipeline.task_dict["trigger_remediation"].python_callable
    summary = {
        "flagged_count": 1,
        "flagged_categories": ["EMAIL"],
        "flagged_columns": [{"column_name": "email", "pii_category": "EMAIL"}],
    }
    mock_hook = MagicMock()
    mock_hook.get_first.return_value = ("owner@company.com",)
    run_conf = _make_conf(
        {"data_source_type": "s3_csv", "source_name": "s3://lake/prod/customers.csv"}
    )

    with (
        patch("orchestration.dags.dag_sampling_pipeline.PostgresHook", return_value=mock_hook),
        patch("airflow.api.common.trigger_dag.trigger_dag") as mock_trigger,
    ):
        trigger_fn(run_conf, summary)

    kwargs = mock_trigger.call_args.kwargs
    assert kwargs["dag_id"] == "remediation"
    assert kwargs["run_id"] == "remediation__evt-001"
    conf = kwargs["conf"]
    assert conf["source_s3_path"] == "s3://lake/prod/customers.csv"
    assert conf["source_format"] == "csv"
    assert conf["owner_email"] == "owner@company.com"
    assert conf["flagged_columns"] == summary["flagged_columns"]
    assert {"table_id", "scanner_event_id", "source_name"} <= conf.keys()


def test_trigger_remediation_catalog_tables_only_notify():
    """Glue/Athena tables must not be moved out from under the catalog."""
    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    trigger_fn = sampling_pipeline.task_dict["trigger_remediation"].python_callable
    mock_hook = MagicMock()
    mock_hook.get_first.return_value = (None,)
    with (
        patch("orchestration.dags.dag_sampling_pipeline.PostgresHook", return_value=mock_hook),
        patch("airflow.api.common.trigger_dag.trigger_dag") as mock_trigger,
    ):
        trigger_fn(_make_conf({"data_source_type": "glue"}), {"flagged_count": 1})

    assert mock_trigger.call_args.kwargs["conf"]["source_s3_path"] == ""


def test_trigger_remediation_tolerates_existing_run():
    from airflow.exceptions import DagRunAlreadyExists

    from orchestration.dags.dag_sampling_pipeline import sampling_dag as sampling_pipeline

    trigger_fn = sampling_pipeline.task_dict["trigger_remediation"].python_callable
    mock_hook = MagicMock()
    mock_hook.get_first.return_value = None
    with (
        patch("orchestration.dags.dag_sampling_pipeline.PostgresHook", return_value=mock_hook),
        patch(
            "airflow.api.common.trigger_dag.trigger_dag",
            side_effect=DagRunAlreadyExists(MagicMock(), "x", "y"),
        ),
    ):
        trigger_fn(_make_conf(), {"flagged_count": 1})  # must not raise
