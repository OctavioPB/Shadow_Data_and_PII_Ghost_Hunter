"""
Unit tests for PIIClassifierOperator — S4-02.

S3 reads, HTTP calls to the inference service, and DB writes are all mocked.
Privacy: verifies that raw sample values are never written to the DB.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest
import pyarrow as pa
import pyarrow.parquet as pq


def _make_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df), buf)
    return buf.getvalue()


def _make_operator(**overrides):
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../orchestration/plugins"))
    from operators.pii_classifier_operator import PIIClassifierOperator

    defaults = dict(
        task_id="test_classify",
        scanner_event_id="evt-uuid-001",
        table_id="evt-uuid-001",
        sample_s3_path="s3://staging/samples/evt-uuid-001/sample.parquet",
        columns=[
            {"name": "email", "dtype": "object"},
            {"name": "id", "dtype": "int64"},
        ],
        inference_api_url="http://mock-inference:8001",
        postgres_conn_id="test_db",
    )
    return PIIClassifierOperator(**{**defaults, **overrides})


def _mock_s3_parquet(df: pd.DataFrame) -> MagicMock:
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: _make_parquet_bytes(df))
    }
    return mock_s3


def _mock_inference_response(results: list[dict]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"table_id": "evt-uuid-001", "results": results}
    return mock_resp


# ─── execute() happy path ─────────────────────────────────────────────────────

def test_operator_calls_inference_api():
    """Verify inference service is called with correct payload structure."""
    df = pd.DataFrame({"email": ["a@b.com", "c@d.com"], "id": [1, 2]})
    mock_s3 = _mock_s3_parquet(df)
    mock_resp = _mock_inference_response(
        [
            {"column_id": "evt-uuid-001:email", "pii_category": "EMAIL", "confidence": 0.97, "flagged": True},
            {"column_id": "evt-uuid-001:id", "pii_category": "NONE", "confidence": 0.92, "flagged": False},
        ]
    )

    mock_hook = MagicMock()

    op = _make_operator()
    with (
        patch("operators.pii_classifier_operator.boto3") as mock_boto,
        patch("operators.pii_classifier_operator.PostgresHook", return_value=mock_hook),
        patch("httpx.Client") as mock_http,
    ):
        mock_boto.client.return_value = mock_s3
        mock_http.return_value.__enter__.return_value.post.return_value = mock_resp

        summary = op.execute(context={})

    # Inference must have been called
    mock_http.return_value.__enter__.return_value.post.assert_called_once()
    call_kwargs = mock_http.return_value.__enter__.return_value.post.call_args
    assert call_kwargs.kwargs["json"]["table_id"] == "evt-uuid-001"


def test_operator_returns_correct_summary():
    df = pd.DataFrame({"email": ["a@b.com"], "id": [1]})
    mock_s3 = _mock_s3_parquet(df)
    mock_resp = _mock_inference_response(
        [
            {"column_id": "evt-uuid-001:email", "pii_category": "EMAIL", "confidence": 0.97, "flagged": True},
            {"column_id": "evt-uuid-001:id", "pii_category": "NONE", "confidence": 0.91, "flagged": False},
        ]
    )
    mock_hook = MagicMock()

    op = _make_operator()
    with (
        patch("operators.pii_classifier_operator.boto3") as mock_boto,
        patch("operators.pii_classifier_operator.PostgresHook", return_value=mock_hook),
        patch("httpx.Client") as mock_http,
    ):
        mock_boto.client.return_value = mock_s3
        mock_http.return_value.__enter__.return_value.post.return_value = mock_resp
        summary = op.execute(context={})

    assert summary["flagged_count"] == 1
    assert summary["total_count"] == 2
    assert "EMAIL" in summary["flagged_categories"]


def test_operator_inserts_one_finding_per_column():
    df = pd.DataFrame({"email": ["a@b.com"], "id": [1]})
    mock_s3 = _mock_s3_parquet(df)
    mock_resp = _mock_inference_response(
        [
            {"column_id": "evt-uuid-001:email", "pii_category": "EMAIL", "confidence": 0.97, "flagged": True},
            {"column_id": "evt-uuid-001:id", "pii_category": "NONE", "confidence": 0.91, "flagged": False},
        ]
    )
    mock_hook = MagicMock()

    op = _make_operator()
    with (
        patch("operators.pii_classifier_operator.boto3") as mock_boto,
        patch("operators.pii_classifier_operator.PostgresHook", return_value=mock_hook),
        patch("httpx.Client") as mock_http,
    ):
        mock_boto.client.return_value = mock_s3
        mock_http.return_value.__enter__.return_value.post.return_value = mock_resp
        op.execute(context={})

    # hook.run is called for: 2 findings inserts + 2 column_sample updates + 1 event status update
    assert mock_hook.run.call_count >= 2


# ─── Privacy: raw values must not reach the DB ───────────────────────────────

def test_raw_values_not_written_to_db():
    """The values read from S3 must NOT appear in any DB INSERT call."""
    sentinel = "TOP_SECRET_EMAIL@classified.gov"
    df = pd.DataFrame({"email": [sentinel]})
    mock_s3 = _mock_s3_parquet(df)
    mock_resp = _mock_inference_response(
        [{"column_id": "evt-uuid-001:email", "pii_category": "EMAIL", "confidence": 0.97, "flagged": True}]
    )
    mock_hook = MagicMock()

    op = _make_operator(columns=[{"name": "email", "dtype": "object"}])
    with (
        patch("operators.pii_classifier_operator.boto3") as mock_boto,
        patch("operators.pii_classifier_operator.PostgresHook", return_value=mock_hook),
        patch("httpx.Client") as mock_http,
    ):
        mock_boto.client.return_value = mock_s3
        mock_http.return_value.__enter__.return_value.post.return_value = mock_resp
        op.execute(context={})

    # Inspect all DB calls — none should contain the sentinel value
    for db_call in mock_hook.run.call_args_list:
        all_args = str(db_call)
        assert sentinel not in all_args, (
            f"Sensitive value '{sentinel}' found in DB call — privacy violation"
        )


# ─── Error handling ───────────────────────────────────────────────────────────

def test_operator_raises_on_inference_error():
    df = pd.DataFrame({"email": ["a@b.com"]})
    mock_s3 = _mock_s3_parquet(df)
    mock_resp = MagicMock(status_code=500, text="Internal Server Error")

    op = _make_operator(columns=[{"name": "email", "dtype": "object"}])
    with (
        patch("operators.pii_classifier_operator.boto3") as mock_boto,
        patch("operators.pii_classifier_operator.PostgresHook"),
        patch("httpx.Client") as mock_http,
    ):
        mock_boto.client.return_value = mock_s3
        mock_http.return_value.__enter__.return_value.post.return_value = mock_resp

        with pytest.raises(RuntimeError, match="Inference service returned 500"):
            op.execute(context={})


# ─── Inference payload: values capped at MAX_VALUES_PER_COL ──────────────────

def test_operator_caps_values_per_column():
    """At most 10 values per column are sent to the inference API."""
    from operators.pii_classifier_operator import _MAX_VALUES_PER_COL

    many_rows = pd.DataFrame({"email": [f"u{i}@example.com" for i in range(50)]})
    mock_s3 = _mock_s3_parquet(many_rows)
    mock_resp = _mock_inference_response(
        [{"column_id": "evt-uuid-001:email", "pii_category": "EMAIL", "confidence": 0.97, "flagged": True}]
    )
    mock_hook = MagicMock()

    op = _make_operator(columns=[{"name": "email", "dtype": "object"}])
    with (
        patch("operators.pii_classifier_operator.boto3") as mock_boto,
        patch("operators.pii_classifier_operator.PostgresHook", return_value=mock_hook),
        patch("httpx.Client") as mock_http,
    ):
        mock_boto.client.return_value = mock_s3
        mock_http.return_value.__enter__.return_value.post.return_value = mock_resp
        op.execute(context={})

    # Inspect the payload sent to inference — values must be <= MAX_VALUES_PER_COL
    posted_json = mock_http.return_value.__enter__.return_value.post.call_args.kwargs["json"]
    for col in posted_json["columns"]:
        assert len(col["values"]) <= _MAX_VALUES_PER_COL
