"""ml.inference.model_loader: score aggregation, caching, model sourcing, tar safety."""

from __future__ import annotations

import io
import tarfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ml.inference.model_loader as ml


@pytest.fixture(autouse=True)
def _fresh_cache():
    ml._pipeline, ml._load_duration_s = None, None
    yield
    ml._pipeline, ml._load_duration_s = None, None


def _scores(**labels: float) -> list[dict]:
    return [{"label": k, "score": v} for k, v in labels.items()]


def _fake_clf(outputs: list[list[dict]]) -> MagicMock:
    clf = MagicMock(side_effect=lambda texts, **kw: outputs[: len(texts)])
    return clf


# ─── classify_column ──────────────────────────────────────────────────────────


def test_classify_column_prefers_confident_pii_over_none():
    clf = _fake_clf(
        [
            _scores(NONE=0.9, EMAIL=0.1),
            _scores(EMAIL=0.97, NONE=0.03),
            _scores(PHONE=0.6, NONE=0.4),
        ]
    )
    with patch.object(ml, "get_pipeline", return_value=clf):
        assert ml.classify_column("contact", ["x", "a@b.com", "555"]) == ("EMAIL", 0.97)


def test_classify_column_reports_none_when_no_sample_is_pii():
    clf = _fake_clf([_scores(NONE=0.8, SSN=0.2), _scores(NONE=0.95, SSN=0.05)])
    with patch.object(ml, "get_pipeline", return_value=clf):
        assert ml.classify_column("status", ["active", "pending"]) == ("NONE", 0.95)


def test_classify_column_batches_capped_samples_in_one_call():
    clf = _fake_clf([_scores(NONE=1.0)] * 10)
    with patch.object(ml, "get_pipeline", return_value=clf):
        ml.classify_column("id", [str(i) for i in range(50)], max_samples=10)

    texts = clf.call_args.args[0]
    assert len(texts) == 10 and texts[0] == "id: 0"
    assert clf.call_count == 1  # one batched forward pass
    assert clf.call_args.kwargs["batch_size"] == 10


def test_classify_column_without_values_uses_the_column_name_only():
    clf = _fake_clf([_scores(EMAIL=0.7)])
    with patch.object(ml, "get_pipeline", return_value=clf):
        assert ml.classify_column("email", []) == ("EMAIL", 0.7)
    assert clf.call_args.args[0] == ["email: "]


# ─── get_pipeline caching ─────────────────────────────────────────────────────


def test_get_pipeline_loads_once_even_under_concurrency():
    sentinel = object()
    calls = []

    def slow_load():
        calls.append(1)
        threading.Event().wait(0.05)
        return sentinel, 1.5

    with patch.object(ml, "_load_model", side_effect=slow_load):
        threads = [threading.Thread(target=ml.get_pipeline) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert calls == [1]
    assert ml.get_pipeline() is sentinel
    assert ml.is_loaded() and ml.load_duration_seconds() == 1.5


# ─── _load_model sourcing ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("env_path", "expected_dir", "downloads"),
    [
        ("s3://bucket/v1/model.tar.gz", "/tmp/extracted/model", True),
        ("/models/local", "/models/local", False),
        ("", "ml/models/pii-classifier", False),
    ],
)
def test_load_model_picks_the_model_source(monkeypatch, env_path, expected_dir, downloads):
    monkeypatch.setenv("MODEL_S3_PATH", env_path)
    monkeypatch.setenv("MODEL_DEVICE", "-1")
    fake_tf = MagicMock()
    with (
        patch.dict("sys.modules", {"transformers": fake_tf}),
        patch.object(ml, "_download_from_s3", return_value="/tmp/extracted/model") as dl,
    ):
        clf, duration = ml._load_model()

    assert dl.called is downloads
    fake_tf.AutoTokenizer.from_pretrained.assert_called_once_with(expected_dir)
    kwargs = fake_tf.pipeline.call_args.kwargs
    assert kwargs["top_k"] is None and kwargs["device"] == -1 and kwargs["truncation"]
    assert clf is fake_tf.pipeline.return_value and duration >= 0


# ─── _download_from_s3: tarball handling ──────────────────────────────────────


def _tarball(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _fake_s3(payload: bytes) -> MagicMock:
    s3 = MagicMock()
    s3.download_file.side_effect = lambda Bucket, Key, Filename: Path(Filename).write_bytes(payload)
    return s3


def _download(payload: bytes, tmp_path: Path) -> tuple[str, MagicMock]:
    s3 = _fake_s3(payload)
    fake_boto3 = MagicMock()
    fake_boto3.client.return_value = s3
    with (
        patch.dict("sys.modules", {"boto3": fake_boto3}),
        patch.object(ml.tempfile, "mkdtemp", return_value=str(tmp_path)),
    ):
        return ml._download_from_s3("s3://models-bucket/v2/model.tar.gz"), s3


def test_download_extracts_the_model_directory(tmp_path):
    model_dir, s3 = _download(_tarball({"model/config.json": b"{}"}), tmp_path)

    s3.download_file.assert_called_once()
    assert s3.download_file.call_args.kwargs["Bucket"] == "models-bucket"
    assert s3.download_file.call_args.kwargs["Key"] == "v2/model.tar.gz"
    assert Path(model_dir) == tmp_path / "extracted" / "model"
    assert (Path(model_dir) / "config.json").read_bytes() == b"{}"


def test_download_falls_back_to_extract_root_without_model_dir(tmp_path):
    model_dir, _ = _download(_tarball({"config.json": b"{}"}), tmp_path)
    assert Path(model_dir) == tmp_path / "extracted"


def test_download_rejects_path_traversal_in_tarball(tmp_path):
    """A malicious tarball must not write outside the extraction directory."""
    evil = _tarball({"model/config.json": b"{}", "../../escaped.txt": b"pwned"})
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(tarfile.FilterError):
        _download(evil, work)
    assert not (tmp_path / "escaped.txt").exists()
