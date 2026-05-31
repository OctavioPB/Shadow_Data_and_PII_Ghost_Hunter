"""
Unit tests for etl/anonymizers/spark_anonymizer.py — S5-01.

Uses a local SparkSession (master=local[*]).
PySpark is marked as optional — tests skip if pyspark is not installed.
DB calls are mocked.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, call, patch

import pytest

pyspark = pytest.importorskip("pyspark", reason="pyspark not installed")

from pyspark.sql import SparkSession
from etl.anonymizers.spark_anonymizer import anonymize_dataframe, run_spark_anonymization


@pytest.fixture(scope="module")
def spark():
    session = (
        SparkSession.builder.master("local[1]")
        .appName("test-anonymizer")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


# ─── anonymize_dataframe ──────────────────────────────────────────────────────

def test_anonymize_dataframe_email_column(spark):
    df = spark.createDataFrame(
        [("alice@example.com",), ("bob@test.org",)],
        ["email"],
    )
    flagged = [{"column_name": "email", "pii_category": "EMAIL"}]
    result = anonymize_dataframe(df, flagged)
    rows = [r["email"] for r in result.collect()]
    assert all(re.match(r"^[0-9a-f]{64}$", v) for v in rows), "Email not SHA-256 hashed"


def test_anonymize_dataframe_ssn_column(spark):
    df = spark.createDataFrame([("123-45-6789",), ("987-65-4321",)], ["ssn"])
    flagged = [{"column_name": "ssn", "pii_category": "SSN"}]
    result = anonymize_dataframe(df, flagged)
    rows = [r["ssn"] for r in result.collect()]
    assert all(v == "[REDACTED]" for v in rows)


def test_anonymize_dataframe_credit_card(spark):
    df = spark.createDataFrame([("4111111111111111",)], ["cc"])
    flagged = [{"column_name": "cc", "pii_category": "CREDIT_CARD"}]
    result = anonymize_dataframe(df, flagged)
    row = result.collect()[0]["cc"]
    assert row.startswith("****-****-****-")
    assert row.endswith("1111")


def test_anonymize_dataframe_skips_missing_column(spark):
    df = spark.createDataFrame([(1,), (2,)], ["id"])
    flagged = [{"column_name": "nonexistent_col", "pii_category": "EMAIL"}]
    # Should not raise; column is silently skipped
    result = anonymize_dataframe(df, flagged)
    assert result.columns == ["id"]


def test_anonymize_dataframe_handles_null_values(spark):
    df = spark.createDataFrame([(None,), ("alice@example.com",)], ["email"])
    flagged = [{"column_name": "email", "pii_category": "EMAIL"}]
    result = anonymize_dataframe(df, flagged)
    rows = [r["email"] for r in result.collect()]
    # None input → "[REDACTED]" (from the strategy's null handling)
    assert "[REDACTED]" in rows or all(v is None or len(v) == 64 for v in rows)


def test_anonymize_dataframe_idempotent_email(spark):
    """Applying the EMAIL strategy twice must produce the same hash."""
    df = spark.createDataFrame([("alice@example.com",)], ["email"])
    flagged = [{"column_name": "email", "pii_category": "EMAIL"}]

    first_pass = anonymize_dataframe(df, flagged)
    second_pass = anonymize_dataframe(first_pass, flagged)

    rows_first = [r["email"] for r in first_pass.collect()]
    rows_second = [r["email"] for r in second_pass.collect()]
    assert rows_first == rows_second


# ─── run_spark_anonymization — idempotency at job level ─────────────────────

def test_run_spark_anonymization_skips_if_already_completed():
    with (
        patch("etl.anonymizers.spark_anonymizer._already_anonymized", return_value=True),
        patch("etl.anonymizers.spark_anonymizer.SparkSession") as mock_spark,
    ):
        result = run_spark_anonymization(
            table_id="tbl-001",
            source_path="s3://bucket/key/",
            output_path="s3://bucket/key/",
            flagged_columns=[{"column_name": "email", "pii_category": "EMAIL"}],
            database_url="postgresql://localhost/test",
        )

    assert result["skipped"] is True
    assert result["reason"] == "already_anonymized"
    mock_spark.builder.appName.assert_not_called()


def test_run_spark_anonymization_writes_audit_on_success(spark):
    df_data = [("alice@example.com",), ("bob@test.org",)]
    mock_df = spark.createDataFrame(df_data, ["email"])

    mock_spark_session = MagicMock()
    mock_spark_session.read.parquet.return_value = mock_df
    written_df = MagicMock()
    mock_df_write = MagicMock()

    with (
        patch("etl.anonymizers.spark_anonymizer._already_anonymized", return_value=False),
        patch("etl.anonymizers.spark_anonymizer._write_audit_record") as mock_audit,
        patch("etl.anonymizers.spark_anonymizer.SparkSession") as MockSparkSession,
    ):
        MockSparkSession.builder.appName.return_value.master.return_value\
            .config.return_value.getOrCreate.return_value = mock_spark_session
        mock_spark_session.read.parquet.return_value = mock_df

        # Intercept write
        with patch.object(mock_df.__class__, "write", new_callable=lambda: property(lambda self: MagicMock())):
            run_spark_anonymization(
                table_id="tbl-audit",
                source_path="s3://b/k/",
                output_path="s3://b/k/",
                flagged_columns=[{"column_name": "email", "pii_category": "EMAIL"}],
                database_url="postgresql://localhost/test",
                spark_master="local[1]",
            )

    mock_audit.assert_called_once()
    args = mock_audit.call_args.args
    assert args[1] == "tbl-audit"  # table_id


# ─── Audit record: no raw values ─────────────────────────────────────────────

def test_audit_record_contains_no_raw_values(spark):
    """The audit record must contain only metadata — never individual cell values."""
    captured_details = {}

    def _fake_audit(db_url, table_id, summary):
        captured_details.update(summary)

    mock_spark_session = MagicMock()
    df_data = [("TOP_SECRET@classified.gov",)]
    mock_df = spark.createDataFrame(df_data, ["email"])
    mock_spark_session.read.parquet.return_value = mock_df

    with (
        patch("etl.anonymizers.spark_anonymizer._already_anonymized", return_value=False),
        patch("etl.anonymizers.spark_anonymizer._write_audit_record", side_effect=_fake_audit),
        patch("etl.anonymizers.spark_anonymizer.SparkSession") as MockSparkSession,
    ):
        MockSparkSession.builder.appName.return_value.master.return_value\
            .config.return_value.getOrCreate.return_value = mock_spark_session

        run_spark_anonymization(
            table_id="tbl-priv",
            source_path="s3://b/k/",
            output_path="s3://b/k/",
            flagged_columns=[{"column_name": "email", "pii_category": "EMAIL"}],
            database_url="postgresql://localhost/test",
            spark_master="local[1]",
        )

    details_str = json.dumps(captured_details)
    assert "TOP_SECRET@classified.gov" not in details_str
