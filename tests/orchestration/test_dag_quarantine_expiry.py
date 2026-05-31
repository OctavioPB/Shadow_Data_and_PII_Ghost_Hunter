"""
Unit tests for dag_quarantine_expiry — [S8-04]

Validates:
  - DAG loads without import errors
  - Correct task structure (six tasks in expected order)
  - find_expiring_soon returns only entries past warning threshold
  - find_expired returns only entries past retention window
  - move_to_expired_prefix: correct S3 copy+delete calls, skips invalid paths
  - mark_expired_in_db: correct SQL updates + audit log inserts
  - No PII data values appear in any log call (path-only logging)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

# ─── DAG import guard ─────────────────────────────────────────────────────────

pytest.importorskip("airflow", reason="apache-airflow not installed")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_row(table_id: str, days_ago: int, warning_sent: bool = False, expired: bool = False):
    """Build a fake quarantine_manifest row tuple."""
    quarantined_at = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return (
        table_id,
        f"s3://pii-quarantine/pending/{table_id}/",
        quarantined_at,
        "owner@company.com",
    )


# ─── DAG structure ────────────────────────────────────────────────────────────


def test_dag_loads_without_errors():
    from orchestration.dags.dag_quarantine_expiry import quarantine_expiry_dag
    dag = quarantine_expiry_dag
    assert dag is not None


def test_dag_has_correct_task_ids():
    from airflow.models import DagBag

    dagbag = DagBag(dag_folder="orchestration/dags", include_examples=False)
    assert "quarantine_expiry" in dagbag.dags
    dag = dagbag.dags["quarantine_expiry"]
    task_ids = {t.task_id for t in dag.tasks}
    expected = {
        "find_expiring_soon",
        "send_expiry_warnings",
        "mark_warnings_sent",
        "find_expired",
        "move_to_expired_prefix",
        "mark_expired_in_db",
    }
    assert expected.issubset(task_ids), f"Missing tasks: {expected - task_ids}"


def test_dag_schedule_is_daily():
    from airflow.models import DagBag

    dagbag = DagBag(dag_folder="orchestration/dags", include_examples=False)
    dag = dagbag.dags["quarantine_expiry"]
    assert dag.schedule_interval == "0 6 * * *"


# ─── find_expiring_soon ───────────────────────────────────────────────────────


@patch("orchestration.dags.dag_quarantine_expiry.PostgresHook")
def test_find_expiring_soon_returns_correct_entries(mock_hook_cls):
    from orchestration.dags.dag_quarantine_expiry import quarantine_expiry_dag

    # 24-day-old entry (past the 23-day warning threshold)
    row = _make_row("tbl-001", days_ago=24)
    mock_hook = MagicMock()
    mock_hook.get_records.return_value = [row]
    mock_hook_cls.return_value = mock_hook

    # Directly invoke the task function
    from orchestration.dags.dag_quarantine_expiry import quarantine_expiry_dag as _dag
    # Re-import the inner function via the DAG source
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")

    # Simulate the task function directly
    with patch("orchestration.dags.dag_quarantine_expiry.PostgresHook", return_value=mock_hook):
        results = mod.find_expiring_soon.function()

    assert len(results) == 1
    assert results[0]["table_id"] == "tbl-001"
    assert results[0]["days_remaining"] <= 7


@patch("orchestration.dags.dag_quarantine_expiry.PostgresHook")
def test_find_expiring_soon_returns_empty_when_no_entries(mock_hook_cls):
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")
    mock_hook = MagicMock()
    mock_hook.get_records.return_value = []
    mock_hook_cls.return_value = mock_hook

    with patch("orchestration.dags.dag_quarantine_expiry.PostgresHook", return_value=mock_hook):
        results = mod.find_expiring_soon.function()

    assert results == []


# ─── find_expired ─────────────────────────────────────────────────────────────


@patch("orchestration.dags.dag_quarantine_expiry.PostgresHook")
def test_find_expired_returns_entries_past_retention(mock_hook_cls):
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")

    row = _make_row("tbl-002", days_ago=31)
    mock_hook = MagicMock()
    mock_hook.get_records.return_value = [(row[0], row[1], row[2])]
    mock_hook_cls.return_value = mock_hook

    with patch("orchestration.dags.dag_quarantine_expiry.PostgresHook", return_value=mock_hook):
        results = mod.find_expired.function()

    assert len(results) == 1
    assert results[0]["table_id"] == "tbl-002"
    assert "s3://" in results[0]["s3_path"]


# ─── move_to_expired_prefix ───────────────────────────────────────────────────


@patch("boto3.client")
def test_move_to_expired_prefix_calls_copy_and_delete(mock_boto_client):
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")

    s3_mock = MagicMock()
    mock_boto_client.return_value = s3_mock

    expired = [
        {
            "table_id": "tbl-003",
            "s3_path": "s3://pii-quarantine/pending/tbl-003/",
            "quarantined_at": "2026-04-01T00:00:00+00:00",
        }
    ]

    with patch("orchestration.dags.dag_quarantine_expiry._S3_QUARANTINE_BUCKET", "pii-quarantine"):
        result = mod.move_to_expired_prefix.function(expired)

    assert result == ["tbl-003"]
    s3_mock.copy_object.assert_called_once_with(
        Bucket="pii-quarantine",
        CopySource={"Bucket": "pii-quarantine", "Key": "pending/tbl-003/"},
        Key="expired/tbl-003/",
    )
    s3_mock.delete_object.assert_called_once_with(
        Bucket="pii-quarantine", Key="pending/tbl-003/"
    )


@patch("boto3.client")
def test_move_to_expired_prefix_skips_invalid_s3_path(mock_boto_client):
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")

    s3_mock = MagicMock()
    mock_boto_client.return_value = s3_mock

    expired = [
        {
            "table_id": "tbl-bad",
            "s3_path": "s3://wrong-bucket/pending/tbl-bad/",
            "quarantined_at": "2026-04-01T00:00:00+00:00",
        }
    ]

    with patch("orchestration.dags.dag_quarantine_expiry._S3_QUARANTINE_BUCKET", "pii-quarantine"):
        result = mod.move_to_expired_prefix.function(expired)

    # Invalid path → skipped → not in result, no S3 calls
    assert "tbl-bad" not in result
    s3_mock.copy_object.assert_not_called()


@patch("boto3.client")
def test_move_to_expired_prefix_handles_s3_error_gracefully(mock_boto_client):
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")

    s3_mock = MagicMock()
    s3_mock.copy_object.side_effect = Exception("NoSuchKey")
    mock_boto_client.return_value = s3_mock

    expired = [
        {
            "table_id": "tbl-err",
            "s3_path": "s3://pii-quarantine/pending/tbl-err/",
            "quarantined_at": "2026-04-01T00:00:00+00:00",
        }
    ]

    with patch("orchestration.dags.dag_quarantine_expiry._S3_QUARANTINE_BUCKET", "pii-quarantine"):
        result = mod.move_to_expired_prefix.function(expired)

    # Error → skipped gracefully → not in result, no crash
    assert "tbl-err" not in result


# ─── mark_expired_in_db ───────────────────────────────────────────────────────


@patch("orchestration.dags.dag_quarantine_expiry.PostgresHook")
def test_mark_expired_in_db_updates_manifest_and_writes_audit(mock_hook_cls):
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")

    mock_hook = MagicMock()
    mock_hook_cls.return_value = mock_hook

    with patch("orchestration.dags.dag_quarantine_expiry.PostgresHook", return_value=mock_hook):
        mod.mark_expired_in_db.function(["tbl-004", "tbl-005"])

    # Each table_id gets 2 SQL calls: UPDATE quarantine_manifest + INSERT audit_log
    assert mock_hook.run.call_count == 4
    # Verify the audit log insert is present
    audit_calls = [c for c in mock_hook.run.call_args_list if "audit_log" in c.args[0]]
    assert len(audit_calls) == 2


@patch("orchestration.dags.dag_quarantine_expiry.PostgresHook")
def test_mark_expired_in_db_is_noop_for_empty_list(mock_hook_cls):
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")

    mock_hook = MagicMock()
    mock_hook_cls.return_value = mock_hook

    with patch("orchestration.dags.dag_quarantine_expiry.PostgresHook", return_value=mock_hook):
        mod.mark_expired_in_db.function([])

    mock_hook.run.assert_not_called()


# ─── PII non-logging assertion ────────────────────────────────────────────────


@patch("boto3.client")
def test_move_to_expired_does_not_log_object_content(mock_boto_client, caplog):
    import importlib
    mod = importlib.import_module("orchestration.dags.dag_quarantine_expiry")

    s3_mock = MagicMock()
    mock_boto_client.return_value = s3_mock

    expired = [
        {
            "table_id": "tbl-006",
            "s3_path": "s3://pii-quarantine/pending/tbl-006/",
            "quarantined_at": "2026-04-01T00:00:00+00:00",
        }
    ]

    # Simulate the copy returning some mock object body — it must never appear in logs
    s3_mock.get_object.return_value = {"Body": MagicMock(read=lambda: b"alice@example.com")}

    with caplog.at_level(logging.DEBUG):
        with patch(
            "orchestration.dags.dag_quarantine_expiry._S3_QUARANTINE_BUCKET", "pii-quarantine"
        ):
            mod.move_to_expired_prefix.function(expired)

    # PII sentinel must not appear in any log output
    for record in caplog.records:
        assert "alice@example.com" not in record.message
        assert "123-45-6789" not in record.message
