"""
PIIClassifierOperator — Airflow custom operator for ML classification.

Flow for a single table:
  1. Read the sample parquet from S3 (written by the sampler)
  2. Build a POST /infer payload (column_name + up to 10 sampled values per column)
  3. Call the inference service
  4. Write one pii_findings row per column
  5. Update column_samples.status to 'flagged' or 'clean'
  6. Return a summary dict for downstream branch operator

Privacy guarantee (S4-04):
  - Values read from S3 are used only to build the inference request payload.
  - They are NEVER persisted to the DB, written to logs, or stored in XCom.
  - Only column metadata (name, pii_category, confidence) is logged/stored.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from airflow.models import BaseOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.utils.decorators import apply_defaults

log = logging.getLogger(__name__)

_INFERENCE_API_URL = os.environ.get("INFERENCE_API_URL", "http://pii-inference:8001")
_DB_CONN = "pii_hunter_db"
_MAX_VALUES_PER_COL = 10  # values sent to inference per column


class PIIClassifierOperator(BaseOperator):
    """
    Classify all sampled columns for a given scanner event.

    :param scanner_event_id: UUID of the scanner_event row
    :param table_id:         Logical table identifier (same as scanner_event_id)
    :param sample_s3_path:   S3 URI of the sampled parquet (e.g. s3://bucket/key.parquet)
    :param columns:          List of {name: str, dtype: str} column descriptors
    :param inference_api_url: Override for the inference service base URL
    :param postgres_conn_id:  Airflow connection ID for the metadata DB
    """

    template_fields = ("scanner_event_id", "table_id", "sample_s3_path")

    @apply_defaults
    def __init__(
        self,
        *,
        scanner_event_id: str,
        table_id: str,
        sample_s3_path: str,
        columns: list[dict[str, str]],
        inference_api_url: str = _INFERENCE_API_URL,
        postgres_conn_id: str = _DB_CONN,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.scanner_event_id = scanner_event_id
        self.table_id = table_id
        self.sample_s3_path = sample_s3_path
        self.columns = columns
        self.inference_api_url = inference_api_url
        self.postgres_conn_id = postgres_conn_id

    def execute(self, context: dict) -> dict:
        """Run classification and persist findings. Returns classification summary."""
        # Step 1 — read sample from S3 (values are used only in-memory)
        column_values = self._read_sample_values()

        # Step 2 — build inference payload (values never logged)
        payload = self._build_inference_payload(column_values)

        # Step 3 — call inference service
        findings = self._call_inference(payload)

        # Step 4 — persist to DB (metadata only, never raw values)
        summary = self._persist_findings(findings)

        # Step 5 — update column_samples status
        self._update_column_sample_statuses(findings)

        # Step 6 — update scanner_event status
        self._update_event_status(summary)

        # Return summary (safe for XCom — no raw values)
        log.info(
            "Classification complete: table_id=%s flagged=%d/%d",
            self.table_id,
            summary["flagged_count"],
            summary["total_count"],
        )
        return summary

    # ── Private helpers ──────────────────────────────────────────────────────

    def _read_sample_values(self) -> dict[str, list[str]]:
        """Download parquet from S3 and return {column_name: [values]}."""
        import io
        import boto3
        import pandas as pd

        uri = self.sample_s3_path
        if uri.startswith("s3://"):
            without = uri[len("s3://"):]
            bucket, _, key = without.partition("/")
            s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
            obj = s3.get_object(Bucket=bucket, Key=key)
            df = pd.read_parquet(io.BytesIO(obj["Body"].read()))
        else:
            # Local path for testing
            import pandas as pd
            df = pd.read_parquet(uri)

        # Limit to configured max values; convert to strings
        result: dict[str, list[str]] = {}
        for col in df.columns:
            result[col] = [str(v) for v in df[col].dropna().head(_MAX_VALUES_PER_COL).tolist()]
        return result

    def _build_inference_payload(self, column_values: dict[str, list[str]]) -> dict:
        columns_payload = []
        for col_meta in self.columns:
            col_name = col_meta["name"]
            columns_payload.append(
                {
                    "column_id": f"{self.scanner_event_id}:{col_name}",
                    "column_name": col_name,
                    "values": column_values.get(col_name, []),
                }
            )
        return {"table_id": self.table_id, "columns": columns_payload}

    def _call_inference(self, payload: dict) -> list[dict]:
        """POST to inference service; raise on non-200."""
        url = f"{self.inference_api_url}/infer"
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Inference service returned {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()["results"]

    def _persist_findings(self, findings: list[dict]) -> dict:
        hook = PostgresHook(postgres_conn_id=self.postgres_conn_id)
        flagged_count = 0

        for f in findings:
            flagged = bool(f["flagged"])
            if flagged:
                flagged_count += 1

            status = "flagged" if flagged else "clean"

            hook.run(
                """
                INSERT INTO pii_findings
                    (scanner_event_id, table_id, column_name,
                     pii_category, confidence, flagged, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                parameters=[
                    self.scanner_event_id,
                    self.table_id,
                    # column_id is "{event_id}:{column_name}" — extract column_name
                    f["column_id"].split(":", 1)[-1],
                    f["pii_category"],
                    f["confidence"],
                    flagged,
                    status,
                ],
            )
            # S4-04: log only metadata, never raw values
            log.info(
                "finding_persisted: column=%s pii_category=%s confidence=%.4f flagged=%s",
                f["column_id"].split(":", 1)[-1],
                f["pii_category"],
                f["confidence"],
                flagged,
            )

        return {
            "table_id": self.table_id,
            "scanner_event_id": self.scanner_event_id,
            "total_count": len(findings),
            "flagged_count": flagged_count,
            "flagged_categories": list(
                {f["pii_category"] for f in findings if f["flagged"]}
            ),
        }

    def _update_column_sample_statuses(self, findings: list[dict]) -> None:
        hook = PostgresHook(postgres_conn_id=self.postgres_conn_id)
        for f in findings:
            col_name = f["column_id"].split(":", 1)[-1]
            new_status = "flagged" if f["flagged"] else "clean"
            hook.run(
                """
                UPDATE column_samples
                SET status = %s
                WHERE scanner_event_id = %s AND column_name = %s
                """,
                parameters=[new_status, self.scanner_event_id, col_name],
            )

    def _update_event_status(self, summary: dict) -> None:
        hook = PostgresHook(postgres_conn_id=self.postgres_conn_id)
        new_status = "flagged" if summary["flagged_count"] > 0 else "classified"
        hook.run(
            "UPDATE scanner_events SET status = %s, updated_at = now() WHERE id = %s",
            parameters=[new_status, self.scanner_event_id],
        )
