"""
HuggingFace Dataset wrapper for the PII labeled JSONL files.

Converts each row {"column_name": str, "value": str, "label": str}
into the text format expected by the DistilBERT classifier:

    "{column_name}: {value}"

Labels are mapped to integer IDs via PII_LABELS.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import torch
from torch.utils.data import Dataset
from transformers import PreTrainedTokenizerBase

# Canonical label order — MUST NOT change without retraining
PII_LABELS: list[str] = [
    "ADDRESS",
    "BANK_ACCOUNT",
    "CREDIT_CARD",
    "DATE_OF_BIRTH",
    "EMAIL",
    "FULL_NAME",
    "NONE",
    "PASSPORT",
    "PHONE",
    "SSN",
]

LABEL2ID: dict[str, int] = {lbl: i for i, lbl in enumerate(PII_LABELS)}
ID2LABEL: dict[int, str] = {i: lbl for lbl, i in LABEL2ID.items()}
NUM_LABELS = len(PII_LABELS)


def _format_input(column_name: str, value: str) -> str:
    return f"{column_name}: {value}"


class PIIDataset(Dataset):
    """Tokenised dataset loaded from a JSONL file."""

    def __init__(
        self,
        path: str | Path,
        tokenizer: PreTrainedTokenizerBase,
        max_length: int = 128,
    ) -> None:
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.records: list[dict[str, str]] = []

        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    self.records.append(json.loads(line))

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        row = self.records[idx]
        text = _format_input(row["column_name"], row["value"])
        label_id = LABEL2ID[row["label"]]

        encoding = self.tokenizer(
            text,
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "labels": torch.tensor(label_id, dtype=torch.long),
        }


def load_splits(
    data_dir: str | Path,
    tokenizer: PreTrainedTokenizerBase,
    max_length: int = 128,
) -> tuple[PIIDataset, PIIDataset, PIIDataset]:
    """Return (train, val, test) datasets from *data_dir*."""
    data_dir = Path(data_dir)
    return (
        PIIDataset(data_dir / "train.jsonl", tokenizer, max_length),
        PIIDataset(data_dir / "val.jsonl", tokenizer, max_length),
        PIIDataset(data_dir / "test.jsonl", tokenizer, max_length),
    )
