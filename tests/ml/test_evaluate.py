"""
Unit tests for ml/training/evaluate.py — S3-03.

All model inference is mocked so no GPU or model weights are needed.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ml.training.evaluate import (
    KNOWN_PII_FIXTURES,
    evaluate_fixtures,
    evaluate_test_split,
    run_evaluation,
)
from ml.data.pii_dataset import PII_LABELS


# ─── Fixture list sanity checks ───────────────────────────────────────────────

def test_known_pii_fixtures_has_all_labels():
    fixture_labels = {label for _, _, label in KNOWN_PII_FIXTURES}
    assert fixture_labels == set(PII_LABELS)


def test_known_pii_fixtures_no_raw_pii_in_labels():
    """Label values must be from the canonical set — no typos."""
    for _, _, label in KNOWN_PII_FIXTURES:
        assert label in PII_LABELS, f"Unknown label in fixtures: {label}"


def test_known_pii_fixtures_values_are_strings():
    for col, val, label in KNOWN_PII_FIXTURES:
        assert isinstance(col, str) and col
        assert isinstance(val, str) and val


# ─── evaluate_fixtures ────────────────────────────────────────────────────────

def _perfect_clf_pipeline(inputs):
    """Mock pipeline that always returns the correct label with confidence 1.0."""
    results = []
    for text in inputs:
        # Try to find which label applies from the fixture list
        # We just return all labels with the correct one at 1.0
        scores = [{f"label_{i}": 0.0} for i in range(10)]
        # Return generic all-zeros except correct label — handled via fixture lookup
        scores = [{"label": lbl, "score": 0.0} for lbl in PII_LABELS]
        results.append(scores)
    return results


def _make_perfect_pipeline():
    """
    Returns a callable that matches the evaluate_fixtures expectation:
    given a list of texts, returns list[list[{"label": str, "score": float}]].
    The pipeline always returns the correct label based on the fixture list.
    """
    fixture_map = {
        f"{col}: {val}": label for col, val, label in KNOWN_PII_FIXTURES
    }

    def _pipeline(texts):
        results = []
        for text in texts:
            expected = fixture_map.get(text, "NONE")
            scores = [
                {"label": lbl, "score": 1.0 if lbl == expected else 0.0}
                for lbl in PII_LABELS
            ]
            results.append(scores)
        return results

    return _pipeline


def test_evaluate_fixtures_perfect_model():
    clf = _make_perfect_pipeline()
    result = evaluate_fixtures(clf, threshold=0.85)
    assert result["fixture_accuracy"] == 1.0
    assert result["fixture_total"] == len(KNOWN_PII_FIXTURES)
    assert result["fixture_correct"] == len(KNOWN_PII_FIXTURES)


def test_evaluate_fixtures_returns_per_fixture_results():
    clf = _make_perfect_pipeline()
    result = evaluate_fixtures(clf, threshold=0.85)
    assert len(result["fixtures"]) == len(KNOWN_PII_FIXTURES)
    for f in result["fixtures"]:
        assert set(f.keys()) >= {"column_name", "value", "expected", "predicted", "confidence", "correct"}


def test_evaluate_fixtures_marks_incorrect_predictions():
    def _always_wrong_pipeline(texts):
        return [
            [{"label": lbl, "score": 1.0 if lbl == "NONE" else 0.0} for lbl in PII_LABELS]
            for _ in texts
        ]

    result = evaluate_fixtures(_always_wrong_pipeline, threshold=0.85)
    # At least one fixture should be wrong (all PII fixtures will be misclassified as NONE)
    assert result["fixture_correct"] < result["fixture_total"]


# ─── evaluate_test_split ──────────────────────────────────────────────────────

def _make_test_jsonl(path: Path, n: int = 30) -> None:
    records = []
    labels_cycle = PII_LABELS * (n // len(PII_LABELS) + 1)
    for i in range(n):
        lbl = labels_cycle[i]
        records.append({"column_name": "col", "value": f"val_{i}", "label": lbl})
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_evaluate_test_split_structure():
    def _clf(texts):
        return [
            [{"label": lbl, "score": 1.0 if lbl == "NONE" else 0.0} for lbl in PII_LABELS]
            for _ in texts
        ]

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _make_test_jsonl(p, n=20)
        result = evaluate_test_split(_clf, str(p), threshold=0.85)

    assert "test_samples" in result
    assert "macro_f1" in result
    assert "accuracy" in result
    assert "per_class" in result
    assert "threshold_analysis" in result
    assert result["test_samples"] == 20


def test_evaluate_test_split_threshold_coverage():
    """When model is always confident, coverage must be 1.0."""
    def _confident_clf(texts):
        return [
            [{"label": lbl, "score": 1.0 if lbl == "EMAIL" else 0.0} for lbl in PII_LABELS]
            for _ in texts
        ]

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.jsonl"
        _make_test_jsonl(p, n=10)
        result = evaluate_test_split(_confident_clf, str(p), threshold=0.85)

    assert result["threshold_analysis"]["coverage"] == 1.0


# ─── run_evaluation ───────────────────────────────────────────────────────────

def test_run_evaluation_writes_reports():
    perfect_pipe = _make_perfect_pipeline()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        test_path = tmp_path / "test.jsonl"
        _make_test_jsonl(test_path, n=20)
        report_dir = tmp_path / "reports"

        with patch("ml.training.evaluate._load_pipeline", return_value=perfect_pipe):
            report = run_evaluation(
                model_dir="fake/model",
                test_data_path=str(test_path),
                report_dir=str(report_dir),
                threshold=0.85,
            )

        latest = report_dir / "latest.json"
        assert latest.exists()
        loaded = json.loads(latest.read_text())
        assert "fixture_evaluation" in loaded
        assert "test_split_evaluation" in loaded
        assert "passed" in loaded


def test_run_evaluation_passed_flag_requires_high_f1():
    """passed=True only when both fixture_accuracy >= 0.95 AND macro_f1 >= 0.90."""
    def _bad_clf(texts):
        return [
            [{"label": lbl, "score": 0.5 / len(PII_LABELS)} for lbl in PII_LABELS]
            for _ in texts
        ]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        test_path = tmp_path / "test.jsonl"
        _make_test_jsonl(test_path, n=10)
        report_dir = tmp_path / "reports"

        with patch("ml.training.evaluate._load_pipeline", return_value=_bad_clf):
            report = run_evaluation(
                model_dir="fake/model",
                test_data_path=str(test_path),
                report_dir=str(report_dir),
                threshold=0.85,
            )

    assert report["passed"] is False
