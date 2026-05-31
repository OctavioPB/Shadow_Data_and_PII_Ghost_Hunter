"""
Privacy logging tests for the PII inference service — S4-04.

Verifies that raw sample values NEVER appear in structured log output,
regardless of what values are passed in the request.
"""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ─── Helpers ─────────────────────────────────────────────────────────────────

_SENTINEL_VALUES = [
    "alice@example.com",         # email address
    "123-45-6789",               # SSN
    "4111 1111 1111 1111",       # VISA card
    "SecretPhoneNumber5559999",  # phone
    "João da Silva",             # full name (Portuguese)
]


def _capture_logs(app, payload: dict) -> str:
    """
    Run a POST /infer request and return everything written to stdout
    (structlog writes JSON to stdout by default).
    """
    import sys
    from io import StringIO

    buf = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client.post("/infer", json=payload)
    finally:
        sys.stdout = old_stdout

    return buf.getvalue()


# ─── Tests ───────────────────────────────────────────────────────────────────

@pytest.fixture
def app_with_mock_model():
    with (
        patch("ml.inference.model_loader.get_pipeline"),
        patch("ml.inference.model_loader.is_loaded", return_value=True),
        patch("ml.inference.model_loader.load_duration_seconds", return_value=1.0),
        patch("ml.inference.app.classify_column", return_value=("EMAIL", 0.97)),
    ):
        from ml.inference.app import app
        yield app


def test_raw_values_not_in_log_output(app_with_mock_model):
    """Core S4-04 requirement: no sample value should appear in any log line."""
    payload = {
        "table_id": "tbl-privacy-test",
        "columns": [
            {
                "column_id": "col-1",
                "column_name": "email",
                "values": _SENTINEL_VALUES,
            }
        ],
    }

    log_output = _capture_logs(app_with_mock_model, payload)

    for sentinel in _SENTINEL_VALUES:
        assert sentinel not in log_output, (
            f"Sensitive value '{sentinel}' found in log output — S4-04 violation"
        )


def test_table_id_is_logged(app_with_mock_model):
    """table_id must appear in logs for traceability."""
    payload = {
        "table_id": "tbl-trace-test-xyz",
        "columns": [
            {"column_id": "c1", "column_name": "phone", "values": ["+1-555-000-0000"]}
        ],
    }
    log_output = _capture_logs(app_with_mock_model, payload)
    assert "tbl-trace-test-xyz" in log_output


def test_pii_category_is_logged(app_with_mock_model):
    """Classified category must appear in logs (column_classified event)."""
    payload = {
        "table_id": "tbl-category-test",
        "columns": [
            {"column_id": "c1", "column_name": "email", "values": ["dummy@test.com"]}
        ],
    }
    log_output = _capture_logs(app_with_mock_model, payload)
    assert "EMAIL" in log_output


def test_confidence_is_logged(app_with_mock_model):
    """Confidence score must appear in logs."""
    payload = {
        "table_id": "tbl-conf-test",
        "columns": [
            {"column_id": "c1", "column_name": "ssn", "values": ["000-00-0000"]}
        ],
    }
    log_output = _capture_logs(app_with_mock_model, payload)
    # The mock returns 0.97 as confidence
    assert "0.97" in log_output


def test_multiple_columns_no_value_leakage(app_with_mock_model):
    """Test with multiple columns — none of their values should appear in logs."""
    sensitive_values_per_col = {
        "email": ["victim@bank.com", "ceo@corp.com"],
        "ssn": ["987-65-4321", "111-22-3333"],
        "credit_card": ["5500 0055 5555 5559"],
    }
    payload = {
        "table_id": "tbl-multi-col",
        "columns": [
            {"column_id": f"col-{name}", "column_name": name, "values": vals}
            for name, vals in sensitive_values_per_col.items()
        ],
    }
    log_output = _capture_logs(app_with_mock_model, payload)

    all_values = [v for vals in sensitive_values_per_col.values() for v in vals]
    for val in all_values:
        assert val not in log_output, (
            f"Value '{val}' leaked into logs — S4-04 violation"
        )
