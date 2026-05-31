"""
Idempotency tests for the remediation pipeline — S5-04.

Tests:
  1. Double-run: running anonymization + quarantine twice on the same table_id
     produces identical output (second run is a no-op).
  2. Partial failure simulation: anonymization fails mid-run, then re-run
     completes cleanly.
  3. Double quarantine: second run returns 'already_quarantined' gracefully.

All DB and S3 calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest

from etl.anonymizers.spark_anonymizer import run_spark_anonymization
from etl.quarantine.quarantine_job import run_quarantine


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_db_mock(already_done: bool = False):
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    # For _already_* checks via engine.connect()
    mock_row = (1,) if already_done else None
    mock_conn.execute.return_value.fetchone.return_value = mock_row

    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_conn
    mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return mock_engine, mock_conn


def _make_s3_mock(objects=None):
    objects = objects or [{"Key": "samples/t/file.parquet", "Size": 1024}]
    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": objects}]
    mock_s3.get_paginator.return_value = paginator
    mock_s3.copy_object.return_value = {}
    mock_s3.delete_object.return_value = {}
    return mock_s3


# ─── [S5-04] Anonymization idempotency ───────────────────────────────────────

def test_anonymization_double_run_second_is_noop():
    """Running anonymization twice must skip on the second call."""
    # First run: not yet done
    with (
        patch("etl.anonymizers.spark_anonymizer._already_anonymized", return_value=False),
        patch("etl.anonymizers.spark_anonymizer._write_audit_record"),
        patch("etl.anonymizers.spark_anonymizer.SparkSession") as MockSpark,
    ):
        mock_spark = MagicMock()
        MockSpark.builder.appName.return_value.master.return_value\
            .config.return_value.getOrCreate.return_value = mock_spark
        df = MagicMock()
        df.count.return_value = 10
        mock_spark.read.parquet.return_value = df

        first = run_spark_anonymization(
            table_id="tbl-idem",
            source_path="s3://b/k/",
            output_path="s3://b/k/",
            flagged_columns=[{"column_name": "email", "pii_category": "EMAIL"}],
            database_url="postgresql://localhost/test",
        )

    assert first["skipped"] is False

    # Second run: already done
    with patch("etl.anonymizers.spark_anonymizer._already_anonymized", return_value=True):
        second = run_spark_anonymization(
            table_id="tbl-idem",
            source_path="s3://b/k/",
            output_path="s3://b/k/",
            flagged_columns=[{"column_name": "email", "pii_category": "EMAIL"}],
            database_url="postgresql://localhost/test",
        )

    assert second["skipped"] is True
    assert second["reason"] == "already_anonymized"


def test_anonymization_no_spark_started_on_second_run():
    """SparkSession must NOT be created when the job is skipped."""
    with (
        patch("etl.anonymizers.spark_anonymizer._already_anonymized", return_value=True),
        patch("etl.anonymizers.spark_anonymizer.SparkSession") as MockSpark,
    ):
        run_spark_anonymization(
            table_id="tbl-skip",
            source_path="s3://b/k/",
            output_path="s3://b/k/",
            flagged_columns=[],
            database_url="db_url",
        )

    MockSpark.builder.appName.assert_not_called()


# ─── [S5-04] Quarantine idempotency ──────────────────────────────────────────

def test_quarantine_double_run_second_is_noop():
    """Quarantine job must return 'already_quarantined' on second call."""
    mock_s3 = _make_s3_mock()

    # First run
    mock_engine_1, _ = _make_db_mock(already_done=False)
    with (
        patch("etl.quarantine.quarantine_job._already_quarantined", return_value=False),
        patch("etl.quarantine.quarantine_job.boto3") as mock_boto,
        patch("etl.quarantine.quarantine_job.sa.create_engine", return_value=mock_engine_1),
    ):
        mock_boto.client.return_value = mock_s3
        first = run_quarantine(
            table_id="tbl-qidem",
            source_s3_path="s3://staging/samples/t/",
            flagged_categories=["EMAIL"],
            database_url="db_url",
        )

    assert first["skipped"] is False

    # Second run
    with patch("etl.quarantine.quarantine_job._already_quarantined", return_value=True):
        second = run_quarantine(
            table_id="tbl-qidem",
            source_s3_path="s3://staging/samples/t/",
            flagged_categories=["EMAIL"],
            database_url="db_url",
        )

    assert second["skipped"] is True
    assert second["reason"] == "already_quarantined"


def test_quarantine_s3_not_called_on_second_run():
    """S3 copy/delete must NOT be called when the job is a no-op."""
    mock_s3 = _make_s3_mock()
    with (
        patch("etl.quarantine.quarantine_job._already_quarantined", return_value=True),
        patch("etl.quarantine.quarantine_job.boto3") as mock_boto,
    ):
        mock_boto.client.return_value = mock_s3
        run_quarantine(
            table_id="tbl-skip",
            source_s3_path="s3://staging/samples/t/",
            flagged_categories=["EMAIL"],
            database_url="db_url",
        )

    mock_s3.copy_object.assert_not_called()
    mock_s3.delete_object.assert_not_called()


# ─── [S5-04] Partial failure + re-run ────────────────────────────────────────

def test_anonymization_partial_failure_then_rerun_completes():
    """
    Simulate: first run raises mid-way (Spark write fails).
    Second run detects 'not yet completed' and runs successfully.
    """
    # First run: Spark raises on write
    with (
        patch("etl.anonymizers.spark_anonymizer._already_anonymized", return_value=False),
        patch("etl.anonymizers.spark_anonymizer.SparkSession") as MockSpark,
    ):
        mock_spark = MagicMock()
        MockSpark.builder.appName.return_value.master.return_value\
            .config.return_value.getOrCreate.return_value = mock_spark
        df = MagicMock()
        df.count.return_value = 5
        mock_spark.read.parquet.return_value = df
        # Simulate write failure
        df.withColumn.return_value.write.mode.return_value.parquet.side_effect = (
            OSError("S3 write failed")
        )

        with pytest.raises(OSError):
            run_spark_anonymization(
                table_id="tbl-partial",
                source_path="s3://b/k/",
                output_path="s3://b/k/",
                flagged_columns=[{"column_name": "email", "pii_category": "EMAIL"}],
                database_url="db_url",
            )

    # Second run: write succeeds, audit not yet recorded → runs clean
    with (
        patch("etl.anonymizers.spark_anonymizer._already_anonymized", return_value=False),
        patch("etl.anonymizers.spark_anonymizer._write_audit_record") as mock_audit,
        patch("etl.anonymizers.spark_anonymizer.SparkSession") as MockSpark2,
    ):
        mock_spark2 = MagicMock()
        MockSpark2.builder.appName.return_value.master.return_value\
            .config.return_value.getOrCreate.return_value = mock_spark2
        df2 = MagicMock()
        df2.count.return_value = 5
        mock_spark2.read.parquet.return_value = df2
        df2.withColumn.return_value = df2  # chain

        result = run_spark_anonymization(
            table_id="tbl-partial",
            source_path="s3://b/k/",
            output_path="s3://b/k/",
            flagged_columns=[{"column_name": "email", "pii_category": "EMAIL"}],
            database_url="db_url",
        )

    assert result["skipped"] is False
    mock_audit.assert_called_once()


def test_quarantine_partial_failure_then_rerun_completes():
    """
    Simulate: first quarantine run fails during S3 copy.
    Second run detects 'not yet quarantined' and runs successfully.
    """
    mock_s3_fail = MagicMock()
    mock_s3_fail.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Key": "s/t/f.parquet", "Size": 100}]}
    ]
    mock_s3_fail.copy_object.side_effect = OSError("S3 network error")

    # First run: copy fails
    with (
        patch("etl.quarantine.quarantine_job._already_quarantined", return_value=False),
        patch("etl.quarantine.quarantine_job.boto3") as mock_boto,
    ):
        mock_boto.client.return_value = mock_s3_fail
        with pytest.raises(OSError):
            run_quarantine(
                table_id="tbl-pfail",
                source_s3_path="s3://staging/s/t/",
                flagged_categories=["EMAIL"],
                database_url="db_url",
            )

    # Second run: copy succeeds (manifest not yet written → runs clean)
    mock_s3_ok = _make_s3_mock()
    mock_engine, mock_conn = _make_db_mock(already_done=False)

    with (
        patch("etl.quarantine.quarantine_job._already_quarantined", return_value=False),
        patch("etl.quarantine.quarantine_job.boto3") as mock_boto2,
        patch("etl.quarantine.quarantine_job.sa.create_engine", return_value=mock_engine),
    ):
        mock_boto2.client.return_value = mock_s3_ok
        result = run_quarantine(
            table_id="tbl-pfail",
            source_s3_path="s3://staging/s/t/",
            flagged_categories=["EMAIL"],
            database_url="db_url",
        )

    assert result["skipped"] is False
    assert result["file_count"] == 1
