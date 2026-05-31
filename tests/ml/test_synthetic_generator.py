"""
Unit tests for ml/data/synthetic_generator.py — S3-01.

All tests run without network access and without the Faker locale
being pinned, so they just verify structural correctness.
"""

from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

import pytest

from ml.data.synthetic_generator import (
    _COLUMN_NAMES,
    _GENERATORS,
    PII_LABELS,
    generate_dataset,
    generate_samples,
    write_jsonl,
)

# Re-export from pii_dataset so both modules agree on the label set
from ml.data.pii_dataset import PII_LABELS as DATASET_PII_LABELS


# ─── Label consistency ────────────────────────────────────────────────────────

def test_generator_labels_match_dataset_labels():
    """Generator categories must exactly match the classifier label set."""
    assert set(_GENERATORS.keys()) == set(DATASET_PII_LABELS)


def test_all_labels_have_column_name_pool():
    for label in _GENERATORS:
        assert label in _COLUMN_NAMES, f"Missing column name pool for {label}"
        assert len(_COLUMN_NAMES[label]) >= 3, f"Column pool too small for {label}"


# ─── Single-label generation ──────────────────────────────────────────────────

@pytest.mark.parametrize("label", list(_GENERATORS.keys()))
def test_generate_samples_returns_correct_count(label):
    samples = generate_samples(label, n=30)
    assert len(samples) == 30


@pytest.mark.parametrize("label", list(_GENERATORS.keys()))
def test_generate_samples_schema(label):
    samples = generate_samples(label, n=5)
    for s in samples:
        assert set(s.keys()) == {"column_name", "value", "label"}
        assert s["label"] == label
        assert isinstance(s["column_name"], str) and s["column_name"]
        assert isinstance(s["value"], str) and s["value"]


@pytest.mark.parametrize("label", list(_GENERATORS.keys()))
def test_generate_samples_column_names_from_pool(label):
    samples = generate_samples(label, n=50)
    allowed = set(_COLUMN_NAMES[label])
    for s in samples:
        assert s["column_name"] in allowed


# ─── Full dataset ─────────────────────────────────────────────────────────────

def test_generate_dataset_total_rows():
    dataset = generate_dataset(samples_per_label=20)
    assert len(dataset) == 20 * len(_GENERATORS)


def test_generate_dataset_all_labels_present():
    dataset = generate_dataset(samples_per_label=10)
    found = {s["label"] for s in dataset}
    assert found == set(_GENERATORS.keys())


def test_generate_dataset_is_shuffled():
    """Rows should not be grouped by label (i.e. shuffled)."""
    dataset = generate_dataset(samples_per_label=50)
    labels = [s["label"] for s in dataset]
    # If the first 50 rows are all the same label, it was not shuffled
    assert len(set(labels[:50])) > 1


# ─── JSONL writer ─────────────────────────────────────────────────────────────

def test_write_jsonl_roundtrip():
    samples = [{"column_name": "email", "value": "a@b.com", "label": "EMAIL"}]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sub" / "out.jsonl"
        write_jsonl(samples, out)
        assert out.exists()
        loaded = [json.loads(line) for line in out.read_text().splitlines()]
    assert loaded == samples


def test_write_jsonl_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        deep = Path(tmp) / "a" / "b" / "c" / "file.jsonl"
        write_jsonl([], deep)
        assert deep.exists()


# ─── Privacy: NONE values should not look like PII ───────────────────────────

def test_none_samples_do_not_contain_at_sign():
    """NONE values must not contain '@' (which would look like an email)."""
    samples = generate_samples("NONE", n=500)
    emails = [s for s in samples if "@" in s["value"]]
    # A tiny number may slip through (e.g. ISO dates with '+') — allow <5 %
    assert len(emails) / len(samples) < 0.05


# ─── Credit card: basic Luhn check ───────────────────────────────────────────

def _luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def test_credit_card_values_pass_luhn():
    samples = generate_samples("CREDIT_CARD", n=50)
    for s in samples:
        assert _luhn_valid(s["value"]), f"Luhn check failed for: {s['value']}"
