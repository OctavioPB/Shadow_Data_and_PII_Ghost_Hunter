"""
Lazy-loading model cache for the PII inference service.

The model is downloaded from S3 on first use, extracted, and kept in memory
for all subsequent requests (process-level singleton, thread-safe).

Environment variables:
    MODEL_S3_PATH   Full S3 URI, e.g. s3://pii-hunter-models/v20260101/model.tar.gz
                    If this is a local filesystem path, it is used directly (dev mode).
    AWS_REGION      AWS region for S3 client
    MODEL_CONFIDENCE_THRESHOLD  Float threshold for flagging (default 0.85)
"""

from __future__ import annotations

import logging
import os
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from ml.data.pii_dataset import PII_LABELS

log = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD: float = float(os.environ.get("MODEL_CONFIDENCE_THRESHOLD", "0.85"))

_lock = threading.Lock()
_pipeline = None          # HuggingFace text-classification pipeline
_load_duration_s: float | None = None


def is_loaded() -> bool:
    return _pipeline is not None


def load_duration_seconds() -> float | None:
    return _load_duration_s


def get_pipeline():
    """Return the cached pipeline, loading it from S3/disk on first call."""
    global _pipeline, _load_duration_s
    if _pipeline is not None:
        return _pipeline
    with _lock:
        if _pipeline is not None:
            return _pipeline
        _pipeline, _load_duration_s = _load_model()
    return _pipeline


def _load_model() -> tuple:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

    model_path = os.environ.get("MODEL_S3_PATH", "")

    t0 = time.monotonic()
    if model_path.startswith("s3://"):
        model_dir = _download_from_s3(model_path)
    elif model_path:
        model_dir = model_path
    else:
        # Fallback: try the default local path (useful in dev with pre-trained model)
        model_dir = "ml/models/pii-classifier"

    log.info("Loading PII classifier from %s", model_dir)
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir)

    clf = pipeline(
        "text-classification",
        model=model,
        tokenizer=tokenizer,
        device=-1,  # CPU; override with GPU index via env if needed
        return_all_scores=True,
        truncation=True,
        max_length=128,
    )
    duration = time.monotonic() - t0
    log.info("Model loaded in %.2fs", duration)
    return clf, duration


def _download_from_s3(s3_uri: str) -> str:
    import boto3

    # Parse s3://bucket/key
    without_scheme = s3_uri[len("s3://"):]
    bucket, _, key = without_scheme.partition("/")

    region = os.environ.get("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)

    tmp_dir = tempfile.mkdtemp(prefix="pii_model_")
    tarball = os.path.join(tmp_dir, "model.tar.gz")

    log.info("Downloading model from %s", s3_uri)
    s3.download_file(Bucket=bucket, Key=key, Filename=tarball)

    extract_dir = os.path.join(tmp_dir, "extracted")
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(extract_dir)  # noqa: S202 — controlled internal path

    # The tarball contains a single "model/" directory
    model_dir = os.path.join(extract_dir, "model")
    if not Path(model_dir).exists():
        # Fallback: use the extract root
        model_dir = extract_dir

    return model_dir


def classify_column(
    column_name: str,
    values: list[str],
    max_samples: int = 10,
) -> tuple[str, float]:
    """
    Classify a column by running inference on up to *max_samples* values.

    Returns (pii_category, confidence) aggregated as max-confidence prediction.
    Privacy: raw values are never logged — only the resulting label/confidence.
    """
    clf = get_pipeline()

    # Sample a subset of values for inference
    sample_values = values[:max_samples] if values else [""]

    texts = [f"{column_name}: {v}" for v in sample_values]
    raw_outputs = clf(texts)

    # raw_outputs: list[list[{"label": str, "score": float}]]
    best_label = "NONE"
    best_score = 0.0

    for scores in raw_outputs:
        score_map = {s["label"]: s["score"] for s in scores}
        top_label = max(score_map, key=score_map.__getitem__)
        top_score = score_map[top_label]

        # Prefer non-NONE predictions when they are more confident
        if top_label != "NONE" and top_score > best_score:
            best_label = top_label
            best_score = top_score
        elif top_label == "NONE" and best_label == "NONE" and top_score > best_score:
            best_score = top_score

    return best_label, round(best_score, 4)
