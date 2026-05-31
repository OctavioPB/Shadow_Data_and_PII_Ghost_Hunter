"""
Evaluation pipeline for the PII classifier.

Runs inference against a known fixture set (hardcoded PII patterns) and
a held-out test split, then writes a JSON report to ml/reports/.

Checks:
  - Per-class precision / recall / F1
  - Threshold analysis: at confidence=0.85, verify recall per class
  - Known-PII fixture regression (VISA card, Brazilian CPF, US SSN, CURP)

Usage:
    python -m ml.training.evaluate \
        --model-dir ml/models/pii-classifier \
        --test-data ml/data/labeled/test.jsonl \
        --report-dir ml/reports \
        --threshold 0.85
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import classification_report, confusion_matrix
from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

from ml.data.pii_dataset import ID2LABEL, LABEL2ID, PII_LABELS

# ─── Known PII Fixtures ───────────────────────────────────────────────────────
# Each fixture is (column_name, value, expected_label).
# These are canonical patterns that must always be classified correctly.

KNOWN_PII_FIXTURES: list[tuple[str, str, str]] = [
    # US Social Security Numbers
    ("ssn", "123-45-6789", "SSN"),
    ("social_security_number", "001-02-0003", "SSN"),
    ("num_seguro_social", "456-78-9012", "SSN"),
    # Brazilian CPF
    ("cpf", "123.456.789-09", "SSN"),
    ("numero_cpf", "987.654.321-00", "SSN"),
    # Mexican CURP
    ("curp", "BADD110313HCMLNS09", "SSN"),
    # VISA / Mastercard / Amex
    ("credit_card", "4532015112830366", "CREDIT_CARD"),
    ("card_number", "4111 1111 1111 1111", "CREDIT_CARD"),
    ("cc_number", "5500005555555559", "CREDIT_CARD"),
    ("numero_tarjeta", "378282246310005", "CREDIT_CARD"),
    ("numero_cartao", "6011111111111117", "CREDIT_CARD"),
    # Emails
    ("email", "alice@example.com", "EMAIL"),
    ("correo", "usuario@dominio.com.mx", "EMAIL"),
    ("email_address", "joao.silva@empresa.com.br", "EMAIL"),
    # Phone numbers
    ("phone", "+1-800-555-0199", "PHONE"),
    ("telefone", "+55 11 91234-5678", "PHONE"),
    ("celular", "+52 55 1234 5678", "PHONE"),
    # Full names
    ("full_name", "María García López", "FULL_NAME"),
    ("nome", "João da Silva", "FULL_NAME"),
    ("customer_name", "John Michael Smith", "FULL_NAME"),
    # Dates of birth
    ("date_of_birth", "1990-07-14", "DATE_OF_BIRTH"),
    ("data_nascimento", "14/07/1990", "DATE_OF_BIRTH"),
    ("fecha_nacimiento", "14-07-1990", "DATE_OF_BIRTH"),
    # Addresses
    ("address", "123 Main St, Springfield, IL 62701", "ADDRESS"),
    ("endereco", "Rua das Flores, 42, São Paulo, SP", "ADDRESS"),
    ("direccion", "Calle Insurgentes 500, CDMX", "ADDRESS"),
    # Bank accounts
    ("iban", "GB29NWBK60161331926819", "BANK_ACCOUNT"),
    ("conta_bancaria", "0001-12345678-9", "BANK_ACCOUNT"),
    ("bank_account", "021000021/1234567890", "BANK_ACCOUNT"),
    # Passports
    ("passport_number", "A12345678", "PASSPORT"),
    ("numero_pasaporte", "AB1234567", "PASSPORT"),
    ("numero_passaporte", "AB1234567", "PASSPORT"),
    # NONE — should not be flagged as PII
    ("product_id", "SKU-0042", "NONE"),
    ("status", "active", "NONE"),
    ("price", "99.99", "NONE"),
    ("quantity", "3", "NONE"),
    ("country_code", "BRA", "NONE"),
]


# ─── Inference helpers ────────────────────────────────────────────────────────

def _load_pipeline(model_dir: str, device: int = -1):
    """Load a HuggingFace text-classification pipeline."""
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir)
    return pipeline(
        "text-classification",
        model=model,
        tokenizer=tokenizer,
        device=device,
        return_all_scores=True,
        truncation=True,
        max_length=128,
    )


def _classify_batch(
    clf_pipeline,
    inputs: list[str],
    batch_size: int = 64,
) -> list[dict[str, float]]:
    """Return a list of {label: confidence} dicts, one per input."""
    results = []
    for i in range(0, len(inputs), batch_size):
        batch = inputs[i : i + batch_size]
        raw = clf_pipeline(batch)
        for scores in raw:
            results.append({s["label"]: s["score"] for s in scores})
    return results


# ─── Fixture evaluation ───────────────────────────────────────────────────────

def evaluate_fixtures(
    clf_pipeline,
    threshold: float,
) -> dict:
    """Run KNOWN_PII_FIXTURES through the model and return per-fixture results."""
    inputs = [f"{col}: {val}" for col, val, _ in KNOWN_PII_FIXTURES]
    score_maps = _classify_batch(clf_pipeline, inputs)

    results = []
    correct = 0
    for (col, val, expected), scores in zip(KNOWN_PII_FIXTURES, score_maps):
        top_label = max(scores, key=scores.__getitem__)
        top_conf = scores[top_label]
        above_threshold = top_conf >= threshold
        is_correct = top_label == expected
        if is_correct:
            correct += 1
        results.append(
            {
                "column_name": col,
                "value": val,
                "expected": expected,
                "predicted": top_label,
                "confidence": round(top_conf, 4),
                "above_threshold": above_threshold,
                "correct": is_correct,
            }
        )

    fixture_accuracy = correct / len(KNOWN_PII_FIXTURES)
    return {
        "fixture_accuracy": round(fixture_accuracy, 4),
        "fixture_total": len(KNOWN_PII_FIXTURES),
        "fixture_correct": correct,
        "fixtures": results,
    }


# ─── Test-split evaluation ────────────────────────────────────────────────────

def evaluate_test_split(
    clf_pipeline,
    test_data_path: str,
    threshold: float,
) -> dict:
    """Evaluate classifier on the held-out test JSONL split."""
    import json as _json

    records: list[dict] = []
    with open(test_data_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(_json.loads(line))

    inputs = [f"{r['column_name']}: {r['value']}" for r in records]
    true_labels = [r["label"] for r in records]

    score_maps = _classify_batch(clf_pipeline, inputs)

    pred_labels = [max(sm, key=sm.__getitem__) for sm in score_maps]
    top_confs = [score_maps[i][pred_labels[i]] for i in range(len(pred_labels))]

    # Standard classification report
    report_dict = classification_report(
        true_labels,
        pred_labels,
        labels=PII_LABELS,
        target_names=PII_LABELS,
        output_dict=True,
        zero_division=0,
    )

    # Threshold analysis: subset where model is confident
    confident_indices = [i for i, c in enumerate(top_confs) if c >= threshold]
    if confident_indices:
        conf_true = [true_labels[i] for i in confident_indices]
        conf_pred = [pred_labels[i] for i in confident_indices]
        threshold_report = classification_report(
            conf_true,
            conf_pred,
            labels=PII_LABELS,
            target_names=PII_LABELS,
            output_dict=True,
            zero_division=0,
        )
        threshold_coverage = len(confident_indices) / len(records)
    else:
        threshold_report = {}
        threshold_coverage = 0.0

    macro_f1 = report_dict["macro avg"]["f1-score"]
    accuracy = report_dict.get("accuracy", 0.0)

    return {
        "test_samples": len(records),
        "macro_f1": round(macro_f1, 4),
        "accuracy": round(accuracy, 4),
        "per_class": report_dict,
        "threshold_analysis": {
            "threshold": threshold,
            "coverage": round(threshold_coverage, 4),
            "confident_samples": len(confident_indices),
            "report": threshold_report,
        },
    }


# ─── Main evaluation routine ──────────────────────────────────────────────────

def run_evaluation(
    model_dir: str,
    test_data_path: str,
    report_dir: str,
    threshold: float = 0.85,
    device: int = -1,
) -> dict:
    """Full evaluation: fixtures + test split → JSON report."""
    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)

    clf_pipeline = _load_pipeline(model_dir, device=device)

    fixture_results = evaluate_fixtures(clf_pipeline, threshold)
    test_results = evaluate_test_split(clf_pipeline, test_data_path, threshold)

    report = {
        "model_dir": str(Path(model_dir).resolve()),
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "threshold": threshold,
        "fixture_evaluation": fixture_results,
        "test_split_evaluation": test_results,
        "passed": (
            fixture_results["fixture_accuracy"] >= 0.95
            and test_results["macro_f1"] >= 0.90
        ),
    }

    out_file = report_path / f"eval_{int(time.time())}.json"
    out_file.write_text(json.dumps(report, indent=2))

    # Also write a stable symlink-style "latest" file
    latest = report_path / "latest.json"
    latest.write_text(json.dumps(report, indent=2))

    print(f"Report written → {out_file}")
    print(f"Fixture accuracy : {fixture_results['fixture_accuracy']:.4f}")
    print(f"Test macro F1    : {test_results['macro_f1']:.4f}")
    print(f"Passed           : {report['passed']}")

    return report


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate PII classifier")
    parser.add_argument("--model-dir", default="ml/models/pii-classifier")
    parser.add_argument("--test-data", default="ml/data/labeled/test.jsonl")
    parser.add_argument("--report-dir", default="ml/reports")
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--device", type=int, default=-1, help="-1 for CPU, 0 for GPU")
    args = parser.parse_args()

    run_evaluation(
        model_dir=args.model_dir,
        test_data_path=args.test_data,
        report_dir=args.report_dir,
        threshold=args.threshold,
        device=args.device,
    )


if __name__ == "__main__":
    main()
