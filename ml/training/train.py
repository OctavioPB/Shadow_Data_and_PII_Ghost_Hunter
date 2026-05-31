"""
Fine-tune distilbert-base-multilingual-cased for PII column classification.

Input format:  "{column_name}: {value}"
Output:        one of 10 PII labels (see ml/data/pii_dataset.py)

Usage:
    python -m ml.training.train \
        --data-dir ml/data/labeled \
        --output-dir ml/models/pii-classifier \
        --mlflow-uri http://localhost:5000 \
        --epochs 5 \
        --batch-size 32

Environment variables (override CLI defaults):
    MLFLOW_TRACKING_URI   MLflow server URI
    MODEL_BASE            HuggingFace model ID (default: distilbert-base-multilingual-cased)
    S3_MODELS_BUCKET      Target S3 bucket (used by publish step, not training)
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import mlflow
import mlflow.pytorch
import numpy as np
import torch
from sklearn.metrics import classification_report, f1_score
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
)

from ml.data.pii_dataset import ID2LABEL, LABEL2ID, NUM_LABELS, load_splits

_DEFAULT_MODEL = "distilbert-base-multilingual-cased"
_MIN_F1_TARGET = 0.90


# ─── Metrics ──────────────────────────────────────────────────────────────────

def _compute_metrics(eval_pred: tuple) -> dict[str, float]:
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    macro_f1 = f1_score(labels, preds, average="macro", zero_division=0)
    weighted_f1 = f1_score(labels, preds, average="weighted", zero_division=0)
    accuracy = float((preds == labels).mean())
    return {
        "macro_f1": macro_f1,
        "weighted_f1": weighted_f1,
        "accuracy": accuracy,
    }


# ─── Training entry point ─────────────────────────────────────────────────────

def train(
    data_dir: str,
    output_dir: str,
    mlflow_uri: str,
    model_base: str = _DEFAULT_MODEL,
    epochs: int = 5,
    batch_size: int = 32,
    learning_rate: float = 2e-5,
    max_length: int = 128,
    warmup_ratio: float = 0.1,
    seed: int = 42,
) -> dict[str, float]:
    """Fine-tune the model and return final evaluation metrics."""
    torch.manual_seed(seed)

    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment("pii-classifier")

    tokenizer = AutoTokenizer.from_pretrained(model_base)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_base,
        num_labels=NUM_LABELS,
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )

    train_ds, val_ds, test_ds = load_splits(data_dir, tokenizer, max_length)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    training_args = TrainingArguments(
        output_dir=str(output_path / "checkpoints"),
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size * 2,
        learning_rate=learning_rate,
        warmup_ratio=warmup_ratio,
        weight_decay=0.01,
        evaluation_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        greater_is_better=True,
        logging_steps=100,
        fp16=torch.cuda.is_available(),
        seed=seed,
        report_to=[],  # disable HF default integrations; we use MLflow directly
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        compute_metrics=_compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=2)],
    )

    with mlflow.start_run(run_name=f"train-{int(time.time())}") as run:
        mlflow.log_params(
            {
                "model_base": model_base,
                "epochs": epochs,
                "batch_size": batch_size,
                "learning_rate": learning_rate,
                "warmup_ratio": warmup_ratio,
                "max_length": max_length,
                "train_size": len(train_ds),
                "val_size": len(val_ds),
                "test_size": len(test_ds),
            }
        )

        trainer.train()

        # ── Per-class evaluation on test set ──────────────────────────────────
        test_preds_output = trainer.predict(test_ds)
        preds = np.argmax(test_preds_output.predictions, axis=-1)
        labels = test_preds_output.label_ids

        report_dict = classification_report(
            labels,
            preds,
            target_names=list(ID2LABEL.values()),
            output_dict=True,
            zero_division=0,
        )

        macro_f1 = report_dict["macro avg"]["f1-score"]
        weighted_f1 = report_dict["weighted avg"]["f1-score"]
        accuracy = report_dict["accuracy"]

        mlflow.log_metrics(
            {
                "test_macro_f1": macro_f1,
                "test_weighted_f1": weighted_f1,
                "test_accuracy": accuracy,
            }
        )

        # Log per-class F1 scores
        for label_name, metrics in report_dict.items():
            if isinstance(metrics, dict) and "f1-score" in metrics:
                safe_name = label_name.replace(" ", "_").lower()
                mlflow.log_metric(f"test_f1_{safe_name}", metrics["f1-score"])

        # Save model artifact
        trainer.save_model(str(output_path))
        tokenizer.save_pretrained(str(output_path))

        # Persist evaluation report
        report_path = output_path / "eval_report.json"
        full_metrics = {
            "macro_f1": macro_f1,
            "weighted_f1": weighted_f1,
            "accuracy": accuracy,
            "per_class": report_dict,
            "mlflow_run_id": run.info.run_id,
        }
        report_path.write_text(json.dumps(full_metrics, indent=2))
        mlflow.log_artifact(str(report_path))

        # Log model to MLflow registry
        mlflow.pytorch.log_model(
            pytorch_model=model,
            artifact_path="model",
            registered_model_name="pii-classifier",
        )

    if macro_f1 < _MIN_F1_TARGET:
        print(
            f"WARNING: macro F1 {macro_f1:.4f} is below target {_MIN_F1_TARGET}. "
            "Consider more epochs or more training data."
        )

    return full_metrics


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Train PII classifier")
    parser.add_argument("--data-dir", default="ml/data/labeled")
    parser.add_argument("--output-dir", default="ml/models/pii-classifier")
    parser.add_argument(
        "--mlflow-uri",
        default=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"),
    )
    parser.add_argument(
        "--model-base",
        default=os.environ.get("MODEL_BASE", _DEFAULT_MODEL),
    )
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    metrics = train(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        mlflow_uri=args.mlflow_uri,
        model_base=args.model_base,
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        max_length=args.max_length,
        seed=args.seed,
    )

    print(f"\nTest macro F1: {metrics['macro_f1']:.4f}")
    print(f"Test accuracy: {metrics['accuracy']:.4f}")


if __name__ == "__main__":
    main()
