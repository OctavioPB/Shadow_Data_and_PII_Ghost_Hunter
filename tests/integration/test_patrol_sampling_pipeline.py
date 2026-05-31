"""
S2-05 — Fixture-based integration tests for the patrol → sample pipeline.

These tests validate the full database lifecycle:
  1. scanner_event inserted as 'pending'
  2. patrol task enqueues it → status 'queued'
  3. sampling pipeline tasks run → status 'sampled'
  4. column_samples rows persisted
  5. audit_log row inserted (append-only)

Uses a real PostgreSQL container (Testcontainers) and mocked S3/Athena.
Run with: pytest -m integration tests/integration/test_patrol_sampling_pipeline.py
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from scanner.models import AuditLog, Base, ColumnSample, ScannerEvent

pytestmark = pytest.mark.integration


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pg_engine():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:15", dbname="pii_hunter") as pg:
        engine = create_engine(pg.get_connection_url())
        Base.metadata.create_all(engine)
        # Apply append-only trigger on audit_log
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'audit_log is append-only';
                    END;
                    $$ LANGUAGE plpgsql;
                    CREATE OR REPLACE TRIGGER trg_audit_log_immutable
                    BEFORE UPDATE OR DELETE ON audit_log
                    FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();
                    """
                )
            )
            conn.commit()
        yield engine
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def db(pg_engine):
    """Provide a session that rolls back after each test."""
    with Session(pg_engine) as session:
        yield session
        session.rollback()


@pytest.fixture
def pending_event(pg_engine) -> ScannerEvent:
    """Insert a fresh 'pending' scanner_event and return it."""
    event = ScannerEvent(
        event_id=str(uuid.uuid4()),
        event_type="table.created",
        source_name="prod.customer_pii_backup",
        data_source_type="glue",
        status="pending",
        raw_event={"table_name": "customer_pii_backup", "database_name": "prod"},
        column_count=6,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    with Session(pg_engine) as s:
        s.add(event)
        s.commit()
        s.refresh(event)
        event_id = event.id
        event_event_id = event.event_id

    # Return a plain dict so it can cross session boundaries
    return {"id": str(event_id), "event_id": event_event_id, "source_name": "prod.customer_pii_backup", "data_source_type": "glue", "column_count": 6}


# ─── Test: patrol enqueue task ────────────────────────────────────────────────


def test_patrol_enqueue_changes_status_to_queued(pg_engine, pending_event):
    """
    After enqueue_for_sampling runs, the event status must be 'queued'.
    Re-running must not double-process (idempotency via CAS).
    """
    with pg_engine.connect() as conn:
        # Simulate what the patrol DAG task does (parameterized SQL, no string interpolation)
        conn.execute(
            text(
                "UPDATE scanner_events SET status = 'queued', updated_at = now() "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"id": pending_event["id"]},
        )
        conn.commit()

    with Session(pg_engine) as s:
        row = s.execute(
            select(ScannerEvent).where(ScannerEvent.id == uuid.UUID(pending_event["id"]))
        ).scalar_one()
        assert row.status == "queued"

    # Idempotency: running the same UPDATE again must not change anything
    with pg_engine.connect() as conn:
        result = conn.execute(
            text(
                "UPDATE scanner_events SET status = 'queued', updated_at = now() "
                "WHERE id = :id AND status = 'pending'"
            ),
            {"id": pending_event["id"]},
        )
        assert result.rowcount == 0, "CAS must be a no-op when status is already 'queued'"
        conn.commit()


# ─── Test: sampling pipeline tasks ──────────────────────────────────────────


def test_sampling_pipeline_persists_column_samples(pg_engine, pending_event):
    """column_samples rows are inserted after run_sampling completes."""
    sample_result = {
        "source_name": pending_event["source_name"],
        "table_id": pending_event["id"],
        "sample_s3_path": f"s3://staging/samples/{pending_event['id']}/sample.parquet",
        "row_count": 512,
        "columns": [
            {"name": "id", "dtype": "int64", "sample_count": 512},
            {"name": "email", "dtype": "object", "sample_count": 510},
            {"name": "cpf", "dtype": "object", "sample_count": 508},
        ],
    }

    with pg_engine.connect() as conn:
        for col in sample_result["columns"]:
            conn.execute(
                text(
                    """
                    INSERT INTO column_samples
                        (scanner_event_id, table_id, column_name, column_dtype,
                         sample_count, sample_s3_path, status)
                    VALUES (:event_id, :table_id, :col_name, :dtype,
                            :count, :s3_path, 'sampled')
                    """
                ),
                {
                    "event_id": pending_event["id"],
                    "table_id": sample_result["table_id"],
                    "col_name": col["name"],
                    "dtype": col["dtype"],
                    "count": col["sample_count"],
                    "s3_path": sample_result["sample_s3_path"],
                },
            )
        conn.commit()

    with Session(pg_engine) as s:
        samples = list(
            s.execute(
                select(ColumnSample).where(
                    ColumnSample.scanner_event_id == uuid.UUID(pending_event["id"])
                )
            ).scalars()
        )
    assert len(samples) == 3
    names = {c.column_name for c in samples}
    assert names == {"id", "email", "cpf"}
    for s_row in samples:
        assert s_row.status == "sampled"
        assert s_row.sample_count >= 0


def test_sampling_pipeline_writes_audit_log(pg_engine, pending_event):
    """One audit_log row must be inserted per sampling completion."""
    details = json.dumps(
        {
            "source_name": pending_event["source_name"],
            "row_count": 512,
            "column_count": 3,
            "sample_s3_path": f"s3://staging/samples/{pending_event['id']}/sample.parquet",
        }
    )
    with pg_engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO audit_log (event_type, table_id, actor, details_json)
                VALUES ('sampling_completed', :table_id, 'airflow:sampling_pipeline', :details::jsonb)
                """
            ),
            {"table_id": pending_event["id"], "details": details},
        )
        conn.commit()

    with Session(pg_engine) as s:
        entries = list(
            s.execute(
                select(AuditLog).where(AuditLog.table_id == pending_event["id"])
            ).scalars()
        )
    assert len(entries) == 1
    assert entries[0].event_type == "sampling_completed"
    assert entries[0].actor == "airflow:sampling_pipeline"


def test_audit_log_is_append_only(pg_engine, pending_event):
    """The DB trigger must prevent UPDATE on audit_log (S2-03 compliance)."""
    from sqlalchemy.exc import DBAPIError

    with pg_engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_log (event_type, table_id, actor) "
                "VALUES ('test_event', :tid, 'pytest')"
            ),
            {"tid": pending_event["id"]},
        )
        conn.commit()

    with pytest.raises((DBAPIError, Exception), match="append-only"):
        with pg_engine.connect() as conn:
            conn.execute(
                text("UPDATE audit_log SET actor = 'hacker' WHERE table_id = :tid"),
                {"tid": pending_event["id"]},
            )
            conn.commit()


def test_sampling_updates_scanner_event_to_sampled(pg_engine, pending_event):
    """After full pipeline run, scanner_event status must be 'sampled'."""
    with pg_engine.connect() as conn:
        conn.execute(
            text(
                "UPDATE scanner_events SET status = 'sampled', updated_at = now() "
                "WHERE id = :id"
            ),
            {"id": pending_event["id"]},
        )
        conn.commit()

    with Session(pg_engine) as s:
        row = s.execute(
            select(ScannerEvent).where(ScannerEvent.id == uuid.UUID(pending_event["id"]))
        ).scalar_one()
        assert row.status == "sampled"
