"""
PySpark anonymization job — S5-01.

Reads a parquet dataset from S3, applies per-column PII anonymization
strategies, writes the result back, and records an audit entry.

Idempotency guarantee:
  - Each strategy function detects already-anonymized values and skips them.
  - An existing audit_log row with event_type='anonymization_completed'
    for the same table_id causes the job to exit early (no-op).

Usage (spark-submit):
    spark-submit etl/anonymizers/spark_anonymizer.py \
        --table-id <uuid> \
        --source-path s3://data-lake/path/to/table/ \
        --output-path s3://data-lake/path/to/table/ \
        --flagged-columns '[{"column_name":"email","pii_category":"EMAIL"}]' \
        --database-url postgresql://...

Or via Airflow (called from dag_remediation.py as a Python callable).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

from etl.anonymizers.strategies import anonymize_value, get_strategy

log = logging.getLogger(__name__)


# ─── Idempotency check ────────────────────────────────────────────────────────

def _already_anonymized(database_url: str, table_id: str) -> bool:
    """Return True if an anonymization_completed record exists for this table."""
    import sqlalchemy as sa

    engine = sa.create_engine(database_url)
    with engine.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT 1 FROM audit_log "
                "WHERE event_type = 'anonymization_completed' AND table_id = :tid "
                "LIMIT 1"
            ),
            {"tid": table_id},
        ).fetchone()
    return row is not None


# ─── Core anonymization logic ─────────────────────────────────────────────────

def anonymize_dataframe(
    df,  # pyspark.sql.DataFrame
    flagged_columns: list[dict[str, str]],
):
    """
    Apply anonymization UDFs to *df* for each flagged column.

    *flagged_columns* is a list of {"column_name": str, "pii_category": str}.
    Columns not in *df* are silently skipped (table schema may have changed).
    Returns the transformed DataFrame.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    existing_cols = set(df.columns)

    for spec in flagged_columns:
        col_name = spec["column_name"]
        pii_category = spec["pii_category"]

        if col_name not in existing_cols:
            log.warning("Column %r not in DataFrame — skipping", col_name)
            continue

        strategy = get_strategy(pii_category)
        # Register as UDF (Python function → Spark UDF)
        udf_fn = F.udf(lambda v, s=strategy: s(v), StringType())

        df = df.withColumn(col_name, udf_fn(F.col(col_name).cast("string")))
        log.info("Anonymized column %r (%s)", col_name, pii_category)

    return df


def run_spark_anonymization(
    table_id: str,
    source_path: str,
    output_path: str,
    flagged_columns: list[dict[str, str]],
    database_url: str,
    app_name: str = "pii-ghost-hunter-anonymizer",
    spark_master: str = "local[*]",
) -> dict[str, Any]:
    """
    Full anonymization pipeline:
      1. Idempotency check
      2. Read parquet from *source_path*
      3. Apply anonymization
      4. Write to *output_path* (overwrite)
      5. Write audit record
    Returns a summary dict (safe to log — no raw values).
    """
    from pyspark.sql import SparkSession

    # ── Idempotency: skip if already completed ────────────────────────────────
    if _already_anonymized(database_url, table_id):
        log.info("Anonymization already completed for table_id=%s — skipping", table_id)
        return {"table_id": table_id, "skipped": True, "reason": "already_anonymized"}

    spark = (
        SparkSession.builder.appName(app_name)
        .master(spark_master)
        .config("spark.sql.adaptive.enabled", "true")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    try:
        # ── Read ─────────────────────────────────────────────────────────────
        df = spark.read.parquet(source_path)
        original_count = df.count()
        log.info("Read %d rows from %s", original_count, source_path)

        # ── Transform ─────────────────────────────────────────────────────────
        df_anon = anonymize_dataframe(df, flagged_columns)

        # ── Write (overwrite) ─────────────────────────────────────────────────
        df_anon.write.mode("overwrite").parquet(output_path)
        log.info("Wrote anonymized data to %s", output_path)

        # ── Audit record ──────────────────────────────────────────────────────
        summary = {
            "table_id": table_id,
            "source_path": source_path,
            "output_path": output_path,
            "row_count": original_count,
            "anonymized_columns": [c["column_name"] for c in flagged_columns],
            "pii_categories": list({c["pii_category"] for c in flagged_columns}),
            "skipped": False,
        }
        _write_audit_record(database_url, table_id, summary)

        return summary

    finally:
        spark.stop()


def _write_audit_record(database_url: str, table_id: str, summary: dict) -> None:
    import sqlalchemy as sa

    engine = sa.create_engine(database_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO audit_log (event_type, table_id, actor, details_json)
                VALUES ('anonymization_completed', :tid, 'etl:spark_anonymizer', :details::jsonb)
                """
            ),
            {
                "tid": table_id,
                "details": json.dumps(
                    {
                        k: v
                        for k, v in summary.items()
                        # Exclude paths from audit — they contain no PII but can be large
                        if k not in ("source_path", "output_path")
                    }
                ),
            },
        )


# ─── CLI entry point ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="PySpark PII anonymizer")
    parser.add_argument("--table-id", required=True)
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--output-path", required=True)
    parser.add_argument(
        "--flagged-columns",
        required=True,
        help='JSON list: [{"column_name": "email", "pii_category": "EMAIL"}, ...]',
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
    )
    parser.add_argument("--spark-master", default="local[*]")
    args = parser.parse_args()

    if not args.database_url:
        raise ValueError("DATABASE_URL must be set (env or --database-url)")

    flagged = json.loads(args.flagged_columns)
    result = run_spark_anonymization(
        table_id=args.table_id,
        source_path=args.source_path,
        output_path=args.output_path,
        flagged_columns=flagged,
        database_url=args.database_url,
        spark_master=args.spark_master,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
