"""
Unit tests for ml/inference/app.py — S4-01.

The model pipeline is mocked so no GPU or model weights are needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from ml.data.pii_dataset import PII_LABELS


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _mock_classify(label: str = "EMAIL", confidence: float = 0.97):
    """Return a classify_column mock that always yields (label, confidence)."""
    return MagicMock(return_value=(label, confidence))


@pytest.fixture
def client():
    with (
        patch("ml.inference.model_loader.get_pipeline"),
        patch("ml.inference.model_loader.is_loaded", return_value=True),
        patch("ml.inference.model_loader.load_duration_seconds", return_value=2.1),
    ):
        from ml.inference.app import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ─── GET /health ─────────────────────────────────────────────────────────────

def test_health_returns_200(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "model_loaded" in body


# ─── GET /metrics ─────────────────────────────────────────────────────────────

def test_metrics_endpoint_returns_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "pii_inference" in resp.text


# ─── POST /infer — happy path ─────────────────────────────────────────────────

def _infer_payload(n_cols: int = 2) -> dict:
    return {
        "table_id": "tbl-001",
        "columns": [
            {
                "column_id": f"col-{i}",
                "column_name": "email",
                "values": ["alice@example.com", "bob@test.org"],
            }
            for i in range(n_cols)
        ],
    }


def test_infer_returns_results_for_each_column(client):
    with patch(
        "ml.inference.app.classify_column",
        side_effect=lambda column_name, values: ("EMAIL", 0.97),
    ):
        resp = client.post("/infer", json=_infer_payload(3))

    assert resp.status_code == 200
    body = resp.json()
    assert body["table_id"] == "tbl-001"
    assert len(body["results"]) == 3


def test_infer_result_schema(client):
    with patch("ml.inference.app.classify_column", return_value=("EMAIL", 0.97)):
        resp = client.post("/infer", json=_infer_payload(1))

    result = resp.json()["results"][0]
    assert set(result.keys()) == {"column_id", "pii_category", "confidence", "flagged"}


def test_infer_flagged_true_above_threshold(client):
    with patch("ml.inference.app.classify_column", return_value=("SSN", 0.96)):
        with patch("ml.inference.model_loader.CONFIDENCE_THRESHOLD", 0.85):
            resp = client.post("/infer", json=_infer_payload(1))

    result = resp.json()["results"][0]
    assert result["pii_category"] == "SSN"
    assert result["flagged"] is True


def test_infer_flagged_false_below_threshold(client):
    with patch("ml.inference.app.classify_column", return_value=("EMAIL", 0.60)):
        with patch("ml.inference.model_loader.CONFIDENCE_THRESHOLD", 0.85):
            resp = client.post("/infer", json=_infer_payload(1))

    assert resp.json()["results"][0]["flagged"] is False


def test_infer_flagged_false_for_none_category(client):
    with patch("ml.inference.app.classify_column", return_value=("NONE", 0.99)):
        resp = client.post("/infer", json=_infer_payload(1))

    assert resp.json()["results"][0]["flagged"] is False


# ─── POST /infer — validation ─────────────────────────────────────────────────

def test_infer_rejects_empty_column_list(client):
    resp = client.post("/infer", json={"table_id": "t", "columns": []})
    assert resp.status_code == 422


def test_infer_rejects_more_than_50_columns(client):
    payload = {
        "table_id": "t",
        "columns": [
            {"column_id": str(i), "column_name": "c", "values": []}
            for i in range(51)
        ],
    }
    resp = client.post("/infer", json=payload)
    assert resp.status_code == 422


def test_infer_handles_empty_values_list(client):
    """Columns with no sampled values should still return a result."""
    with patch("ml.inference.app.classify_column", return_value=("NONE", 0.55)):
        resp = client.post(
            "/infer",
            json={
                "table_id": "t",
                "columns": [{"column_id": "c1", "column_name": "mystery_col", "values": []}],
            },
        )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 1


# ─── POST /infer — error handling ─────────────────────────────────────────────

def test_infer_returns_500_on_model_error(client):
    with patch("ml.inference.app.classify_column", side_effect=RuntimeError("GPU OOM")):
        resp = client.post("/infer", json=_infer_payload(1))
    assert resp.status_code == 500
