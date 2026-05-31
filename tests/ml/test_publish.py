"""
Unit tests for ml/training/publish.py — S3-04.

S3 and DB calls are mocked — no AWS credentials or Postgres needed.
"""

from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from ml.training.publish import _create_tarball, register_model, upload_to_s3


# ─── _create_tarball ──────────────────────────────────────────────────────────

def test_create_tarball_produces_gz_file():
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text('{"a": 1}')
        (model_dir / "pytorch_model.bin").write_bytes(b"\x00" * 16)

        dest = Path(tmp) / "model.tar.gz"
        result = _create_tarball(str(model_dir), str(dest))

        assert result == str(dest)
        assert dest.exists()
        with tarfile.open(str(dest), "r:gz") as tar:
            names = tar.getnames()
        assert any("config.json" in n for n in names)


def test_create_tarball_uses_model_arcname():
    """Root entry in tarball must be 'model', not the full path."""
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "my-long-path" / "pii-classifier"
        model_dir.mkdir(parents=True)
        (model_dir / "file.txt").write_text("x")

        dest = Path(tmp) / "out.tar.gz"
        _create_tarball(str(model_dir), str(dest))

        with tarfile.open(str(dest), "r:gz") as tar:
            names = tar.getnames()
        # Every entry must start with 'model'
        assert all(n.startswith("model") for n in names)


# ─── upload_to_s3 ─────────────────────────────────────────────────────────────

def test_upload_to_s3_targets_correct_key():
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")

        mock_s3 = MagicMock()
        with patch("ml.training.publish.boto3") as mock_boto:
            mock_boto.client.return_value = mock_s3
            uri = upload_to_s3(str(model_dir), "v1.0.0", "pii-hunter-models", "us-east-1")

    assert uri == "s3://pii-hunter-models/v1.0.0/model.tar.gz"
    mock_s3.upload_file.assert_called_once()
    call_kwargs = mock_s3.upload_file.call_args.kwargs
    assert call_kwargs["Bucket"] == "pii-hunter-models"
    assert call_kwargs["Key"] == "v1.0.0/model.tar.gz"


def test_upload_to_s3_uses_server_side_encryption():
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "model"
        model_dir.mkdir()
        (model_dir / "f").write_text("x")

        mock_s3 = MagicMock()
        with patch("ml.training.publish.boto3") as mock_boto:
            mock_boto.client.return_value = mock_s3
            upload_to_s3(str(model_dir), "v2.0.0", "bucket", "us-east-1")

    extra_args = mock_s3.upload_file.call_args.kwargs.get("ExtraArgs", {})
    assert extra_args.get("ServerSideEncryption") == "AES256"


def test_upload_does_not_write_to_source_bucket():
    """Model upload must only target the models bucket, never a data bucket."""
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = Path(tmp) / "model"
        model_dir.mkdir()
        (model_dir / "f").write_text("x")

        mock_s3 = MagicMock()
        with patch("ml.training.publish.boto3") as mock_boto:
            mock_boto.client.return_value = mock_s3
            upload_to_s3(str(model_dir), "v1.0.0", "pii-hunter-models", "us-east-1")

    for c in mock_s3.upload_file.call_args_list:
        bucket = c.kwargs.get("Bucket", "")
        assert "data-lake" not in bucket
        assert "source" not in bucket


# ─── register_model ───────────────────────────────────────────────────────────

def test_register_model_executes_insert():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.scalar_one.return_value = 7

    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_conn

    with patch("ml.training.publish.sa.create_engine", return_value=mock_engine):
        row_id = register_model(
            database_url="postgresql://localhost/test",
            version="v1.0.0",
            s3_uri="s3://pii-hunter-models/v1.0.0/model.tar.gz",
            metrics={"macro_f1": 0.93, "weighted_f1": 0.94, "accuracy": 0.95, "fixture_accuracy": 0.97},
            status="candidate",
        )

    assert row_id == 7
    sql_text = str(mock_conn.execute.call_args.args[0])
    assert "INSERT" in sql_text.upper()
    assert "UPDATE" not in sql_text.upper()
    assert "DELETE" not in sql_text.upper()


def test_register_model_passes_correct_params():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.return_value.scalar_one.return_value = 1

    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_conn

    metrics = {"macro_f1": 0.91, "weighted_f1": 0.92, "accuracy": 0.93, "fixture_accuracy": 0.96}
    with patch("ml.training.publish.sa.create_engine", return_value=mock_engine):
        register_model("db_url", "v2.0.0", "s3://bucket/key", metrics, "approved")

    params = mock_conn.execute.call_args.args[1]
    assert params["version"] == "v2.0.0"
    assert params["status"] == "approved"
    assert params["macro_f1"] == 0.91
