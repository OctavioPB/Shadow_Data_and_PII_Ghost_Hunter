"""
Unit tests for etl/quarantine/quarantine_job.py — S5-02.

S3 and DB calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from etl.quarantine.quarantine_job import (
    _already_quarantined,
    _move_s3_prefix,
    run_quarantine,
)


# ─── _move_s3_prefix ─────────────────────────────────────────────────────────

def _make_mock_s3(objects: list[dict]) -> MagicMock:
    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": objects}]
    mock_s3.get_paginator.return_value = paginator
    mock_s3.copy_object.return_value = {}
    mock_s3.delete_object.return_value = {}
    return mock_s3


def test_move_s3_prefix_copies_and_deletes():
    objects = [
        {"Key": "samples/tbl-001/part-0.parquet", "Size": 1024},
        {"Key": "samples/tbl-001/part-1.parquet", "Size": 2048},
    ]
    mock_s3 = _make_mock_s3(objects)

    count, total = _move_s3_prefix(
        mock_s3, "staging-bucket", "samples/tbl-001/",
        "pii-quarantine", "pending/tbl-001/",
    )

    assert count == 2
    assert total == 3072
    assert mock_s3.copy_object.call_count == 2
    assert mock_s3.delete_object.call_count == 2


def test_move_s3_prefix_server_side_encryption():
    objects = [{"Key": "samples/tbl/file.parquet", "Size": 512}]
    mock_s3 = _make_mock_s3(objects)

    _move_s3_prefix(mock_s3, "src-bucket", "samples/tbl/", "pii-quarantine", "pending/tbl/")

    copy_kwargs = mock_s3.copy_object.call_args.kwargs
    assert copy_kwargs.get("ServerSideEncryption") == "AES256"


def test_move_s3_prefix_dest_bucket_is_quarantine():
    """Copies must go to the quarantine bucket, never back to the source."""
    objects = [{"Key": "data/file.parquet", "Size": 100}]
    mock_s3 = _make_mock_s3(objects)

    _move_s3_prefix(mock_s3, "source-bucket", "data/", "pii-quarantine", "pending/t/")

    for c in mock_s3.copy_object.call_args_list:
        assert c.kwargs["Bucket"] == "pii-quarantine"
        assert c.kwargs["Bucket"] != "source-bucket"


def test_move_s3_prefix_returns_zero_for_empty_prefix():
    mock_s3 = _make_mock_s3([])
    count, total = _move_s3_prefix(mock_s3, "b", "empty/", "qb", "dest/")
    assert count == 0
    assert total == 0


# ─── run_quarantine — idempotency ─────────────────────────────────────────────

def test_run_quarantine_skips_if_already_quarantined():
    with patch("etl.quarantine.quarantine_job._already_quarantined", return_value=True):
        result = run_quarantine(
            table_id="tbl-001",
            source_s3_path="s3://staging/samples/tbl-001/",
            flagged_categories=["EMAIL"],
            database_url="postgresql://localhost/test",
        )

    assert result["skipped"] is True
    assert result["reason"] == "already_quarantined"


def test_run_quarantine_raises_on_non_s3_path():
    with patch("etl.quarantine.quarantine_job._already_quarantined", return_value=False):
        with pytest.raises(ValueError, match="Expected s3://"):
            run_quarantine(
                table_id="t",
                source_s3_path="/local/path",
                flagged_categories=[],
                database_url="db_url",
            )


# ─── run_quarantine — happy path ──────────────────────────────────────────────

def _setup_db_mock():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_conn
    mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return mock_engine, mock_conn


def test_run_quarantine_writes_manifest():
    mock_engine, mock_conn = _setup_db_mock()
    objects = [{"Key": "samples/t/file.parquet", "Size": 500}]
    mock_s3 = _make_mock_s3(objects)

    with (
        patch("etl.quarantine.quarantine_job._already_quarantined", return_value=False),
        patch("etl.quarantine.quarantine_job.boto3") as mock_boto,
        patch("etl.quarantine.quarantine_job.sa.create_engine", return_value=mock_engine),
    ):
        mock_boto.client.return_value = mock_s3
        result = run_quarantine(
            table_id="tbl-999",
            source_s3_path="s3://staging/samples/t/",
            flagged_categories=["SSN", "EMAIL"],
            database_url="postgresql://localhost/test",
        )

    assert result["file_count"] == 1
    assert result["total_bytes"] == 500
    assert "manifest_id" in result
    assert result["quarantine_s3_path"].startswith("s3://pii-quarantine/")


def test_run_quarantine_updates_pii_findings_status():
    mock_engine, mock_conn = _setup_db_mock()
    objects = [{"Key": "samples/t/f.parquet", "Size": 100}]
    mock_s3 = _make_mock_s3(objects)

    with (
        patch("etl.quarantine.quarantine_job._already_quarantined", return_value=False),
        patch("etl.quarantine.quarantine_job.boto3") as mock_boto,
        patch("etl.quarantine.quarantine_job.sa.create_engine", return_value=mock_engine),
    ):
        mock_boto.client.return_value = mock_s3
        run_quarantine(
            table_id="tbl-upd",
            source_s3_path="s3://staging/samples/t/",
            flagged_categories=["CREDIT_CARD"],
            database_url="postgresql://localhost/test",
        )

    # At least one execute call must reference pii_findings and 'quarantined'
    all_sqls = " ".join(
        str(c.args[0]) for c in mock_conn.execute.call_args_list
    )
    assert "pii_findings" in all_sqls
    assert "quarantined" in all_sqls


def test_run_quarantine_writes_audit_log():
    mock_engine, mock_conn = _setup_db_mock()
    objects = [{"Key": "s/t/f.parquet", "Size": 100}]
    mock_s3 = _make_mock_s3(objects)

    with (
        patch("etl.quarantine.quarantine_job._already_quarantined", return_value=False),
        patch("etl.quarantine.quarantine_job.boto3") as mock_boto,
        patch("etl.quarantine.quarantine_job.sa.create_engine", return_value=mock_engine),
    ):
        mock_boto.client.return_value = mock_s3
        run_quarantine(
            table_id="tbl-audit",
            source_s3_path="s3://staging/s/t/",
            flagged_categories=["EMAIL"],
            database_url="postgresql://localhost/test",
        )

    all_sqls = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
    assert "audit_log" in all_sqls
    assert "quarantine_completed" in all_sqls
