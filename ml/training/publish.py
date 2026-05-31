"""
Package the trained model and publish it to S3.

Steps:
  1. tar.gz the model directory
  2. Upload to s3://{bucket}/{version}/model.tar.gz
  3. Register the version in the model_registry DB table

Usage:
    python -m ml.training.publish \
        --model-dir ml/models/pii-classifier \
        --report   ml/reports/latest.json \
        --version  v1.0.0

Environment variables:
    S3_MODELS_BUCKET   Target S3 bucket (required)
    AWS_REGION         AWS region
    DATABASE_URL       PostgreSQL connection string (required)
"""

from __future__ import annotations

import argparse
import json
import os
import tarfile
import tempfile
import time
from pathlib import Path

import boto3
import botocore
import sqlalchemy as sa

# ─── S3 packaging ─────────────────────────────────────────────────────────────

def _create_tarball(model_dir: str, dest_path: str) -> str:
    """Create a .tar.gz of *model_dir* at *dest_path* and return *dest_path*."""
    with tarfile.open(dest_path, "w:gz") as tar:
        tar.add(model_dir, arcname="model")
    return dest_path


def upload_to_s3(
    model_dir: str,
    version: str,
    bucket: str,
    region: str,
) -> str:
    """Package and upload model artifact; return the S3 URI."""
    s3_key = f"{version}/model.tar.gz"
    s3_uri = f"s3://{bucket}/{s3_key}"

    with tempfile.TemporaryDirectory() as tmp:
        tarball = os.path.join(tmp, "model.tar.gz")
        _create_tarball(model_dir, tarball)

        s3 = boto3.client("s3", region_name=region)
        s3.upload_file(
            Filename=tarball,
            Bucket=bucket,
            Key=s3_key,
            ExtraArgs={"ServerSideEncryption": "AES256"},
        )

    return s3_uri


# ─── Model registry ───────────────────────────────────────────────────────────

def register_model(
    database_url: str,
    version: str,
    s3_uri: str,
    metrics: dict,
    status: str = "candidate",
) -> int:
    """Insert a row into model_registry and return the new row id."""
    engine = sa.create_engine(database_url, future=True)
    with engine.begin() as conn:
        result = conn.execute(
            sa.text(
                """
                INSERT INTO model_registry
                    (version, s3_uri, macro_f1, weighted_f1, accuracy,
                     fixture_accuracy, status, trained_at)
                VALUES
                    (:version, :s3_uri, :macro_f1, :weighted_f1, :accuracy,
                     :fixture_accuracy, :status, now())
                RETURNING id
                """
            ),
            {
                "version": version,
                "s3_uri": s3_uri,
                "macro_f1": metrics.get("macro_f1"),
                "weighted_f1": metrics.get("weighted_f1"),
                "accuracy": metrics.get("accuracy"),
                "fixture_accuracy": metrics.get("fixture_accuracy"),
                "status": status,
            },
        )
        return result.scalar_one()


# ─── Publish entry point ──────────────────────────────────────────────────────

def publish(
    model_dir: str,
    report_path: str,
    version: str,
    bucket: str,
    region: str,
    database_url: str,
    status: str = "candidate",
) -> dict:
    """Full publish pipeline: package → S3 → DB registration."""
    report = json.loads(Path(report_path).read_text())

    metrics = {
        "macro_f1": report["test_split_evaluation"].get("macro_f1"),
        "weighted_f1": report["test_split_evaluation"]
        .get("per_class", {})
        .get("weighted avg", {})
        .get("f1-score"),
        "accuracy": report["test_split_evaluation"].get("accuracy"),
        "fixture_accuracy": report["fixture_evaluation"].get("fixture_accuracy"),
    }

    print(f"Packaging {model_dir} → s3://{bucket}/{version}/model.tar.gz …")
    s3_uri = upload_to_s3(model_dir, version, bucket, region)
    print(f"Uploaded: {s3_uri}")

    row_id = register_model(database_url, version, s3_uri, metrics, status)
    print(f"Registered model version '{version}' in model_registry (id={row_id})")

    return {
        "version": version,
        "s3_uri": s3_uri,
        "metrics": metrics,
        "status": status,
        "registry_id": row_id,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Publish PII classifier to S3 + model registry")
    parser.add_argument("--model-dir", default="ml/models/pii-classifier")
    parser.add_argument("--report", default="ml/reports/latest.json")
    parser.add_argument(
        "--version",
        default=f"v{time.strftime('%Y%m%d')}",
        help="Semantic version string, e.g. v1.2.0",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("S3_MODELS_BUCKET", "pii-hunter-models"),
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection string",
    )
    parser.add_argument(
        "--status",
        choices=["candidate", "approved"],
        default="candidate",
    )
    args = parser.parse_args()

    if not args.database_url:
        raise ValueError("DATABASE_URL must be set (env or --database-url)")

    result = publish(
        model_dir=args.model_dir,
        report_path=args.report,
        version=args.version,
        bucket=args.bucket,
        region=args.region,
        database_url=args.database_url,
        status=args.status,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
