"""
Prometheus metrics for the PII inference service — S4-05.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# Request latency — buckets designed for p50/p95/p99 tracking (0.85 s SLO)
INFERENCE_LATENCY = Histogram(
    "pii_inference_request_duration_seconds",
    "End-to-end latency of POST /infer requests",
    labelnames=["status"],
    buckets=(0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0),
)

INFERENCE_REQUESTS = Counter(
    "pii_inference_requests_total",
    "Total inference requests",
    labelnames=["status"],
)

INFERENCE_COLUMNS = Counter(
    "pii_inference_columns_total",
    "Total column classifications performed",
    labelnames=["pii_category"],
)

MODEL_LOAD_DURATION = Histogram(
    "pii_model_load_duration_seconds",
    "Time to load the model from S3/disk on first request",
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

FLAGGED_COLUMNS = Counter(
    "pii_flagged_columns_total",
    "Total columns flagged as PII (confidence >= threshold)",
    labelnames=["pii_category"],
)
