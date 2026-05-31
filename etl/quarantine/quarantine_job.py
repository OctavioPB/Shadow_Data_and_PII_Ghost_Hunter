"""
Quarantine job — S5-02.

Moves (copy + delete) raw flagged data from the source S3 path to the
isolated quarantine bucket, writes a quarantine_manifest record, and
updates pii_findings status to 'quarantined'.

Security model:
  - Quarantine bucket is write-only for the pipeline (IAM policy enforced in Terraform).
  - Only the 'dpo' IAM role can read from it.
  - This job never reads from the quarantine bucket — only writes to it.

Idempotency:
  - If a quarantine_manifest row already exists for this table_id with
    status='quarantined', the job is a no-op.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import boto3
import botocore
import sqlalchemy as sa

log = logging.getLogger(__name__)

_QUARANTINE_BUCKET = os.environ.get("S3_QUARANTINE_BUCKET", "pii-quarantine")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


# ─── Idempotency check ────────────────────────────────────────────────────────

def _already_quarantined(database_url: str, table_id: str) -> bool:
    engine = sa.create_engine(database_url)
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT 1 FROM quarantine_manifest "
                "WHERE table_id = :tid AND status = 'quarantined' LIMIT 1"
            ),
            {"tid": table_id},
        ).fetchone()
    return row is not None


# ─── S3 helpers ───────────────────────────────────────────────────────────────

def _list_s3_objects(s3_client, bucket: str, prefix: str) -> list[dict]:
    """List all objects under *bucket/prefix*."""
    paginator = s3_client.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects.extend(page.get("Contents", []))
    return objects


def _copy_object(
    s3_client,
    source_bucket: str,
    source_key: str,
    dest_bucket: str,
    dest_key: str,
) -> None:
    s3_client.copy_object(
        CopySource={"Bucket": source_bucket, "Key": source_key},
        Bucket=dest_bucket,
        Key=dest_key,
        ServerSideEncryption="AES256",
    )


def _move_s3_prefix(
    s3_client,
    source_bucket: str,
    source_prefix: str,
    dest_bucket: str,
    dest_prefix: str,
) -> tuple[int, int]:
    """
    Copy all objects under source_prefix to dest_prefix, then delete originals.
    Returns (file_count, total_bytes).
    """
    objects = _list_s3_objects(s3_client, source_bucket, source_prefix)
    if not objects:
        log.warning("No objects found at s3://%s/%s", source_bucket, source_prefix)
        return 0, 0

    total_bytes = 0
    for obj in objects:
        key = obj["Key"]
        relative = key[len(source_prefix):]
        dest_key = dest_prefix.rstrip("/") + "/" + relative.lstrip("/")
        _copy_object(s3_client, source_bucket, key, dest_bucket, dest_key)
        total_bytes += obj.get("Size", 0)
        log.info("Quarantined s3://%s/%s → s3://%s/%s", source_bucket, key, dest_bucket, dest_key)

    # Delete originals from source (move semantics)
    for obj in objects:
        s3_client.delete_object(Bucket=source_bucket, Key=obj["Key"])

    return len(objects), total_bytes


# ─── Core quarantine logic ────────────────────────────────────────────────────

def run_quarantine(
    table_id: str,
    source_s3_path: str,
    flagged_categories: list[str],
    database_url: str,
    quarantine_bucket: str = _QUARANTINE_BUCKET,
    aws_region: str = _AWS_REGION,
) -> dict[str, Any]:
    """
    Move raw data to quarantine, write manifest, update findings status.
    Returns a summary dict (no raw data values — only metadata).
    """
    # ── Idempotency ──────────────────────────────────────────────────────────
    if _already_quarantined(database_url, table_id):
        log.info("Already quarantined: table_id=%s — skipping", table_id)
        return {"table_id": table_id, "skipped": True, "reason": "already_quarantined"}

    # ── Parse source S3 path ─────────────────────────────────────────────────
    if not source_s3_path.startswith("s3://"):
        raise ValueError(f"Expected s3:// URI, got: {source_s3_path!r}")
    without = source_s3_path[len("s3://"):]
    source_bucket, _, source_prefix = without.partition("/")
    dest_prefix = f"pending/{table_id}/"

    s3 = boto3.client("s3", region_name=aws_region)

    # ── Move to quarantine ───────────────────────────────────────────────────
    file_count, total_bytes = _move_s3_prefix(
        s3, source_bucket, source_prefix, quarantine_bucket, dest_prefix
    )
    quarantine_s3_path = f"s3://{quarantine_bucket}/{dest_prefix}"

    # ── Write manifest ───────────────────────────────────────────────────────
    engine = sa.create_engine(database_url)
    manifest_id = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO quarantine_manifest
                    (id, table_id, source_s3_path, quarantine_s3_path,
                     flagged_categories, file_count, total_bytes, status)
                VALUES
                    (:id, :tid, :src, :dest, :cats::jsonb, :fc, :tb, 'quarantined')
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "id": manifest_id,
                "tid": table_id,
                "src": source_s3_path,
                "dest": quarantine_s3_path,
                "cats": json.dumps(flagged_categories),
                "fc": file_count,
                "tb": total_bytes,
            },
        )

        # ── Update pii_findings → 'quarantined' ──────────────────────────────
        conn.execute(
            sa.text(
                "UPDATE pii_findings SET status = 'quarantined' WHERE table_id = :tid"
            ),
            {"tid": table_id},
        )

        # ── Audit log ────────────────────────────────────────────────────────
        conn.execute(
            sa.text(
                """
                INSERT INTO audit_log (event_type, table_id, actor, details_json)
                VALUES ('quarantine_completed', :tid, 'etl:quarantine_job', :details::jsonb)
                """
            ),
            {
                "tid": table_id,
                "details": json.dumps(
                    {
                        "quarantine_s3_path": quarantine_s3_path,
                        "flagged_categories": flagged_categories,
                        "file_count": file_count,
                        "total_bytes": total_bytes,
                        "manifest_id": manifest_id,
                    }
                ),
            },
        )

    log.info(
        "Quarantine complete: table_id=%s files=%d bytes=%d dest=%s",
        table_id,
        file_count,
        total_bytes,
        quarantine_s3_path,
    )

    return {
        "table_id": table_id,
        "quarantine_s3_path": quarantine_s3_path,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "manifest_id": manifest_id,
        "skipped": False,
    }
