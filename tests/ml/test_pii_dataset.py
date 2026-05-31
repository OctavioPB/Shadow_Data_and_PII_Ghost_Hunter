"""
Unit tests for ml/data/pii_dataset.py — PIIDataset and label maps.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import torch

from ml.data.pii_dataset import (
    ID2LABEL,
    LABEL2ID,
    NUM_LABELS,
    PII_LABELS,
    PIIDataset,
    _format_input,
)


# ─── Label map consistency ────────────────────────────────────────────────────

def test_label_maps_are_inverses():
    for lbl, idx in LABEL2ID.items():
        assert ID2LABEL[idx] == lbl


def test_num_labels_matches_list():
    assert NUM_LABELS == len(PII_LABELS)
    assert NUM_LABELS == 10


def test_label_list_is_sorted():
    assert PII_LABELS == sorted(PII_LABELS)


# ─── _format_input ────────────────────────────────────────────────────────────

def test_format_input():
    result = _format_input("email", "alice@example.com")
    assert result == "email: alice@example.com"


# ─── PIIDataset ───────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


@pytest.fixture
def mock_tokenizer():
    tok = MagicMock()
    tok.return_value = {
        "input_ids": torch.zeros(1, 128, dtype=torch.long),
        "attention_mask": torch.ones(1, 128, dtype=torch.long),
    }
    tok.side_effect = None
    # Make tok(...) return expected structure
    result = MagicMock()
    result.__getitem__ = lambda self, key: {
        "input_ids": torch.zeros(1, 128, dtype=torch.long),
        "attention_mask": torch.ones(1, 128, dtype=torch.long),
    }[key]
    tok.return_value = result
    return tok


def test_pii_dataset_length():
    rows = [
        {"column_name": "email", "value": "a@b.com", "label": "EMAIL"},
        {"column_name": "ssn", "value": "123-45-6789", "label": "SSN"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "data.jsonl"
        _write_jsonl(p, rows)

        tok = MagicMock()
        tok.return_value = {
            "input_ids": torch.zeros(1, 128, dtype=torch.long),
            "attention_mask": torch.ones(1, 128, dtype=torch.long),
        }
        ds = PIIDataset(p, tok, max_length=128)

    assert len(ds) == 2


def test_pii_dataset_item_keys():
    rows = [{"column_name": "phone", "value": "+1-800-555-0100", "label": "PHONE"}]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "data.jsonl"
        _write_jsonl(p, rows)

        tok = MagicMock()
        tok.return_value = {
            "input_ids": torch.zeros(1, 128, dtype=torch.long),
            "attention_mask": torch.ones(1, 128, dtype=torch.long),
        }
        ds = PIIDataset(p, tok, max_length=128)
        item = ds[0]

    assert set(item.keys()) == {"input_ids", "attention_mask", "labels"}


def test_pii_dataset_label_encoding():
    rows = [{"column_name": "email", "value": "x@y.com", "label": "EMAIL"}]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "data.jsonl"
        _write_jsonl(p, rows)

        tok = MagicMock()
        tok.return_value = {
            "input_ids": torch.zeros(1, 128, dtype=torch.long),
            "attention_mask": torch.ones(1, 128, dtype=torch.long),
        }
        ds = PIIDataset(p, tok, max_length=128)
        item = ds[0]

    assert item["labels"].item() == LABEL2ID["EMAIL"]


def test_pii_dataset_skips_blank_lines():
    lines = [
        '{"column_name": "ssn", "value": "123-45-6789", "label": "SSN"}',
        "",
        "   ",
        '{"column_name": "email", "value": "a@b.com", "label": "EMAIL"}',
    ]
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "data.jsonl"
        p.write_text("\n".join(lines))

        tok = MagicMock()
        tok.return_value = {
            "input_ids": torch.zeros(1, 128, dtype=torch.long),
            "attention_mask": torch.ones(1, 128, dtype=torch.long),
        }
        ds = PIIDataset(p, tok)

    assert len(ds) == 2
