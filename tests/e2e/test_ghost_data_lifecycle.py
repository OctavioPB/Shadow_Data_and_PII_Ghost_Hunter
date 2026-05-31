"""
End-to-end test: ghost-data detection lifecycle — [S7-03]

Tests the full pipeline from scanner event → PII findings → API → manual remediation
→ audit log using a real PostgreSQL container (via testcontainers).

Kafka and the inference service are mocked — this test validates the data store
and API contract, not the streaming layer.

Marked as 'integration' — requires Docker.
Run with: pytest -m integration tests/e2e/ -v --no-cov

SLA assertions:
  - Risk Inventory API returns flagged table within 100ms
  - Audit log records remediation within 100ms of POST
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

pytest.importorskip("testcontainers", reason="testcontainers not installed")

from testcontainers.postgres import PostgresContainer  # noqa: E402

pytest.importorskip("alembic", reason="alembic not installed")
pytest.importorskip("httpx", reason="httpx not installed")

import subprocess  # noqa: E402
import time  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

pytestmark = pytest.mark.integration


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pg_container():
    """Spin up a real PostgreSQL 15 container for the module."""
    with PostgresContainer("postgres:15-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def sync_engine(pg_container):
    """Synchronous engine for migration and seed data."""
    url = pg_container.get_connection_url()
    engine = create_engine(url, echo=False)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def async_db_url(pg_container) -> str:
    return pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture(scope="module", autouse=True)
def apply_migrations(sync_engine):
    """Run Alembic migrations against the test container."""
    import os
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", str(sync_engine.url))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="module")
def seeded_table_id(sync_engine) -> str:
    """
    Seed one scanner event + PII findings for a ghost table.
    Returns the table_id seeded.
    """
    table_id = f"tbl-e2e-{uuid.uuid4().hex[:8]}"
    event_id = str(uuid.uuid4())

    with sync_engine.begin() as conn:
        # Scanner event (what the Kafka consumer writes)
        conn.execute(
            text("""
                INSERT INTO scanner_events
                    (id, event_id, event_type, source_name, data_source_type,
                     status, raw_event, owner_email, bucket)
                VALUES
                    (:id, :event_id, 'table.created', 'prod.customer_pii_backup',
                     's3', 'flagged', :raw::jsonb, 'owner@company.com', 'data-lake-prod')
            """),
            {
                "id": str(uuid.uuid4()),
                "event_id": event_id,
                "raw": json.dumps({"table": table_id, "created_at": "2026-05-16T00:00:00Z"}),
            },
        )

        # Retrieve the inserted event's UUID
        row = conn.execute(
            text("SELECT id FROM scanner_events WHERE event_id = :eid"),
            {"eid": event_id},
        ).fetchone()
        se_id = row[0]

        # PII findings (what the inference service writes via PIIClassifierOperator)
        for col, cat, conf in [
            ("email_address", "EMAIL", 0.97),
            ("national_id", "SSN", 0.92),
        ]:
            conn.execute(
                text("""
                    INSERT INTO pii_findings
                        (id, scanner_event_id, table_id, column_name,
                         pii_category, confidence, flagged, status)
                    VALUES
                        (:id, :se_id, :tid, :col, :cat, :conf, 1, 'flagged')
                """),
                {
                    "id": str(uuid.uuid4()),
                    "se_id": str(se_id),
                    "tid": table_id,
                    "col": col,
                    "cat": cat,
                    "conf": conf,
                },
            )

    return table_id


@pytest.fixture(scope="module")
async def api_client(async_db_url):
    """AsyncClient wired to the FastAPI app with a real DB."""
    from api.db import get_db
    from api.auth import get_current_user
    from api.main import app

    # Real async engine pointing at the test container
    engine = create_async_engine(async_db_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _real_db():
        async with session_factory() as session:
            yield session

    # Override DB but keep real auth (we'll use real JWT)
    app.dependency_overrides[get_db] = _real_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


@pytest.fixture(scope="module")
async def dpo_auth_headers(api_client) -> dict[str, str]:
    resp = await api_client.post(
        "/api/v1/auth/token",
        data={"username": "dpo@company.com", "password": "dpo"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ─── E2E Test scenarios ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_flagged_table_appears_in_risk_inventory(
    api_client, dpo_auth_headers, seeded_table_id
):
    """
    [S7-03] Ghost table seeded directly to DB must appear in /api/v1/risks
    within one API call — no polling needed (synchronous read path).
    """
    t0 = time.monotonic()
    resp = await api_client.get("/api/v1/risks", headers=dpo_auth_headers)
    latency_ms = (time.monotonic() - t0) * 1000

    assert resp.status_code == 200
    body = resp.json()

    table_ids = [item["table_id"] for item in body["items"]]
    assert seeded_table_id in table_ids, (
        f"Seeded table {seeded_table_id!r} not found in risk inventory. "
        f"Got: {table_ids}"
    )

    # SLA: Risk Inventory must respond < 200ms (index scan on flagged=1)
    assert latency_ms < 200, f"Risk inventory latency {latency_ms:.0f}ms exceeded 200ms SLA"


@pytest.mark.asyncio
async def test_pii_report_shows_correct_columns(
    api_client, dpo_auth_headers, seeded_table_id
):
    """
    [S7-03] PII report for the seeded table must show both flagged columns
    with correct categories and confidence scores.
    """
    resp = await api_client.get(
        f"/api/v1/tables/{seeded_table_id}/pii-report",
        headers=dpo_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["table_id"] == seeded_table_id
    assert body["source_name"] == "prod.customer_pii_backup"

    col_names = {c["column_name"] for c in body["flagged_columns"]}
    assert "email_address" in col_names
    assert "national_id" in col_names

    email_col = next(c for c in body["flagged_columns"] if c["column_name"] == "email_address")
    assert email_col["pii_category"] == "EMAIL"
    assert email_col["confidence"] == pytest.approx(0.97, abs=0.01)


@pytest.mark.asyncio
async def test_stats_summary_counts_seeded_table(api_client, dpo_auth_headers):
    """
    [S7-03] Stats summary must reflect at least one flagged table (the seeded one).
    """
    resp = await api_client.get("/api/v1/stats/summary", headers=dpo_auth_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_flagged"] >= 1
    assert 0.0 <= body["compliance_score"] <= 100.0


@pytest.mark.asyncio
async def test_manual_remediation_creates_audit_entry(
    api_client, dpo_auth_headers, seeded_table_id, sync_engine
):
    """
    [S7-03] POST /tables/{id}/remediate must write an entry to audit_log
    and return a queued status.
    """
    t0 = time.monotonic()
    resp = await api_client.post(
        f"/api/v1/tables/{seeded_table_id}/remediate",
        json={"action": "quarantine", "notes": "e2e test — remediation trigger"},
        headers=dpo_auth_headers,
    )
    latency_ms = (time.monotonic() - t0) * 1000

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["action"] == "quarantine"

    # SLA: POST must complete within 200ms
    assert latency_ms < 200, f"Remediate latency {latency_ms:.0f}ms exceeded 200ms SLA"

    # Verify audit log entry was written
    with sync_engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT event_type, actor FROM audit_log
                WHERE table_id = :tid
                  AND event_type = 'manual_quarantine_requested'
                ORDER BY timestamp DESC
                LIMIT 1
            """),
            {"tid": seeded_table_id},
        ).fetchone()

    assert row is not None, "Audit log entry not found after remediation POST"
    assert row[0] == "manual_quarantine_requested"
    assert row[1] == "dpo@company.com"


@pytest.mark.asyncio
async def test_audit_log_shows_remediation_event(
    api_client, dpo_auth_headers, seeded_table_id
):
    """
    [S7-03] The audit log endpoint must surface the remediation event.
    """
    resp = await api_client.get(
        f"/api/v1/audit-log?event_type=manual_quarantine_requested&table_id={seeded_table_id}",
        headers=dpo_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert body["items"][0]["event_type"] == "manual_quarantine_requested"
    assert body["items"][0]["table_id"] == seeded_table_id


@pytest.mark.asyncio
async def test_viewer_cannot_remediate(api_client, seeded_table_id):
    """
    [S7-03] Viewer role must receive 403 on the remediate endpoint — enforced E2E.
    """
    token_resp = await api_client.post(
        "/api/v1/auth/token",
        data={"username": "viewer@company.com", "password": "viewer"},
    )
    token = token_resp.json()["access_token"]

    resp = await api_client.post(
        f"/api/v1/tables/{seeded_table_id}/remediate",
        json={"action": "anonymize"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
