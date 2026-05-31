"""
Unit tests for S3Sampler and AthenaSampler.

Boto3 calls are always mocked — no AWS credentials required.
S2-03: verify samplers never write to source buckets.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest


# ─── S3Sampler ───────────────────────────────────────────────────────────────


@pytest.fixture
def s3_sampler():
    import sys, os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../orchestration/plugins"))
    from samplers.s3_sampler import S3Sampler

    return S3Sampler(staging_bucket="pii-hunter-staging")


def _parquet_bytes(df: pd.DataFrame) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df), buf)
    return buf.getvalue()


def test_s3_sampler_reads_source_never_writes_to_source(s3_sampler):
    """S2-03: sampler must not call s3.put_object on the source bucket."""
    df = pd.DataFrame({"name": ["Alice", "Bob"], "email": ["a@b.com", "b@c.com"]})
    source_body = _parquet_bytes(df)

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: source_body)}
    mock_s3.put_object.return_value = {}

    with patch("samplers.base_sampler.boto3") as mock_boto:
        mock_boto.client.return_value = mock_s3

        with patch("samplers.s3_sampler.boto3") as mock_boto_s3:
            mock_boto_s3.client.return_value = mock_s3
            s3_sampler.sample(
                source_name="s3://source-bucket/data/file.parquet",
                table_id="tbl-001",
                file_format="parquet",
            )

    # get_object on source bucket — expected
    mock_s3.get_object.assert_called_once_with(Bucket="source-bucket", Key="data/file.parquet")

    # put_object must target ONLY the staging bucket, never the source
    for put_call in mock_s3.put_object.call_args_list:
        assert put_call.kwargs.get("Bucket") == "pii-hunter-staging", (
            "Sampler must only write to staging bucket, not to source"
        )


def test_s3_sampler_limits_to_max_rows(s3_sampler):
    df = pd.DataFrame({"col": range(5_000)})
    source_body = _parquet_bytes(df)

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: source_body)}
    mock_s3.put_object.return_value = {}

    with patch("samplers.s3_sampler.boto3") as mock_s3_boto:
        mock_s3_boto.client.return_value = mock_s3
        with patch("samplers.base_sampler.boto3") as mock_base_boto:
            mock_base_boto.client.return_value = mock_s3
            result = s3_sampler.sample(
                source_name="s3://bucket/file.parquet",
                table_id="tbl-002",
                file_format="parquet",
            )

    assert result.row_count == 1_000
    assert result.row_count <= s3_sampler.MAX_ROWS


def test_s3_sampler_extracts_column_metadata(s3_sampler):
    df = pd.DataFrame(
        {"name": ["Alice"], "age": [30], "email": ["a@b.com"], "score": [0.99]}
    )
    source_body = _parquet_bytes(df)

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: source_body)}
    mock_s3.put_object.return_value = {}

    with patch("samplers.s3_sampler.boto3") as m1:
        m1.client.return_value = mock_s3
        with patch("samplers.base_sampler.boto3") as m2:
            m2.client.return_value = mock_s3
            result = s3_sampler.sample(
                source_name="s3://b/f.parquet",
                table_id="t",
                file_format="parquet",
            )

    col_names = [c.name for c in result.columns]
    assert set(col_names) == {"name", "age", "email", "score"}
    for col in result.columns:
        assert col.sample_count >= 0


def test_s3_sampler_invalid_uri_raises():
    import sys, os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../orchestration/plugins"))
    from samplers.s3_sampler import S3Sampler

    sampler = S3Sampler(staging_bucket="staging")
    with pytest.raises(ValueError, match="Invalid S3 URI"):
        sampler.sample(source_name="not-an-s3-uri", table_id="t")


def test_s3_sampler_unsupported_format_raises(s3_sampler):
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"data")}
    with patch("samplers.s3_sampler.boto3") as m:
        m.client.return_value = mock_s3
        with pytest.raises(ValueError, match="Unsupported file format"):
            s3_sampler.sample(
                source_name="s3://bucket/file.txt",
                table_id="t",
                file_format="xml",
            )


# ─── AthenaSampler ───────────────────────────────────────────────────────────


@pytest.fixture
def athena_sampler():
    import sys, os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../orchestration/plugins"))
    from samplers.athena_sampler import AthenaSampler

    return AthenaSampler(
        staging_bucket="pii-hunter-staging",
        athena_output_location="s3://pii-hunter-staging/athena-results/",
    )


def _mock_athena_client(columns: list[str], rows: list[list[str]]):
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "qe-001"}
    client.get_query_execution.return_value = {
        "QueryExecution": {"Status": {"State": "SUCCEEDED"}}
    }
    client.get_query_results.return_value = {
        "ResultSet": {
            "ResultSetMetadata": {
                "ColumnInfo": [{"Label": c} for c in columns]
            },
            "Rows": [
                {"Data": [{"VarCharValue": c} for c in columns]},  # header
                *[{"Data": [{"VarCharValue": v} for v in row]} for row in rows],
            ],
        }
    }
    return client


def test_athena_sampler_uses_read_only_select(athena_sampler):
    """Query sent to Athena must be a SELECT — never INSERT/UPDATE/DELETE/DROP."""
    mock_athena = _mock_athena_client(["id", "email"], [["1", "a@b.com"]])
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with (
        patch("samplers.athena_sampler.boto3") as mock_boto,
        patch("samplers.base_sampler.boto3") as mock_base_boto,
    ):
        mock_boto.client.return_value = mock_athena
        mock_base_boto.client.return_value = mock_s3
        athena_sampler.sample(source_name="mydb.users", table_id="tbl-003")

    sql: str = mock_athena.start_query_execution.call_args.kwargs["QueryString"]
    assert sql.strip().upper().startswith("SELECT"), "Athena query must be a SELECT"
    assert "INSERT" not in sql.upper()
    assert "UPDATE" not in sql.upper()
    assert "DELETE" not in sql.upper()
    assert "DROP" not in sql.upper()


def test_athena_sampler_query_has_limit(athena_sampler):
    mock_athena = _mock_athena_client(["col"], [["v"]])
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}

    with (
        patch("samplers.athena_sampler.boto3") as m,
        patch("samplers.base_sampler.boto3") as mb,
    ):
        m.client.return_value = mock_athena
        mb.client.return_value = mock_s3
        athena_sampler.sample(source_name="db.table", table_id="t")

    sql: str = mock_athena.start_query_execution.call_args.kwargs["QueryString"]
    assert "LIMIT 1000" in sql.upper()


def test_athena_sampler_raises_on_query_failure(athena_sampler):
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "qe-fail"}
    client.get_query_execution.return_value = {
        "QueryExecution": {
            "Status": {
                "State": "FAILED",
                "StateChangeReason": "Table does not exist",
            }
        }
    }

    with patch("samplers.athena_sampler.boto3") as m:
        m.client.return_value = client
        with pytest.raises(RuntimeError, match="Table does not exist"):
            athena_sampler.sample(source_name="db.missing_table", table_id="t")


# ─── get_sampler factory ─────────────────────────────────────────────────────


def test_get_sampler_returns_athena_for_glue():
    import sys, os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../orchestration/plugins"))
    from samplers import get_sampler
    from samplers.athena_sampler import AthenaSampler

    sampler = get_sampler("glue", staging_bucket="staging")
    assert isinstance(sampler, AthenaSampler)


def test_get_sampler_returns_s3_for_parquet():
    import sys, os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../orchestration/plugins"))
    from samplers import get_sampler
    from samplers.s3_sampler import S3Sampler

    sampler = get_sampler("s3_parquet", staging_bucket="staging")
    assert isinstance(sampler, S3Sampler)


def test_get_sampler_raises_for_unknown():
    import sys, os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../orchestration/plugins"))
    from samplers import get_sampler

    with pytest.raises(ValueError, match="No sampler available"):
        get_sampler("snowflake", staging_bucket="staging")
