"""
Unit tests for risk endpoints — [S6-01].

DB calls are mocked via per-test AsyncMock dependency overrides.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import get_current_user
from api.db import get_db
from api.main import app

# ─── Constants ────────────────────────────────────────────────────────────────

_DPO = {"email": "dpo@company.com", "role": "dpo", "name": "DPO"}
_VIEWER = {"email": "viewer@company.com", "role": "viewer", "name": "Viewer"}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _empty_session():
    """AsyncSession that returns no rows and scalar=0."""
    result = MagicMock()
    result.fetchall.return_value = []
    result.fetchone.return_value = None
    result.scalar.return_value = 0
    session = AsyncMock()
    session.execute.return_value = result
    session.commit = AsyncMock()
    return session


def _stats_session(total_flagged=10, remediated=7, pending_review=3):
    result = MagicMock()
    result.fetchone.return_value = MagicMock(
        total_flagged=total_flagged,
        remediated=remediated,
        pending_review=pending_review,
    )
    result.fetchall.return_value = []
    result.scalar.return_value = 0
    session = AsyncMock()
    session.execute.return_value = result
    return session


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def as_dpo():
    app.dependency_overrides[get_current_user] = lambda: _DPO


@pytest.fixture
def as_viewer():
    app.dependency_overrides[get_current_user] = lambda: _VIEWER


def set_db(session):
    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


# ─── GET /api/v1/risks ────────────────────────────────────────────────────────


async def test_list_risks_returns_empty_when_no_data(client: AsyncClient, as_dpo):
    set_db(_empty_session())
    resp = await client.get("/api/v1/risks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1


async def test_list_risks_requires_auth(client: AsyncClient):
    resp = await client.get("/api/v1/risks")
    assert resp.status_code == 401


async def test_list_risks_page_param_is_reflected(client: AsyncClient, as_dpo):
    set_db(_empty_session())
    resp = await client.get("/api/v1/risks?page=2&size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 2
    assert body["size"] == 10


async def test_list_risks_invalid_page_returns_422(client: AsyncClient, as_dpo):
    resp = await client.get("/api/v1/risks?page=0")
    assert resp.status_code == 422


# ─── GET /api/v1/stats/summary ────────────────────────────────────────────────


async def test_stats_summary_returns_correct_shape(client: AsyncClient, as_dpo):
    set_db(_stats_session())
    resp = await client.get("/api/v1/stats/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "total_flagged" in body
    assert "remediated" in body
    assert "pending_review" in body
    assert "compliance_score" in body


async def test_stats_summary_computes_compliance_score(client: AsyncClient, as_dpo):
    set_db(_stats_session(total_flagged=10, remediated=7, pending_review=3))
    resp = await client.get("/api/v1/stats/summary")
    body = resp.json()
    assert body["compliance_score"] == pytest.approx(70.0, abs=0.1)


async def test_stats_summary_requires_auth(client: AsyncClient):
    resp = await client.get("/api/v1/stats/summary")
    assert resp.status_code == 401


# ─── GET /api/v1/tables/{id}/pii-report ──────────────────────────────────────


async def test_pii_report_returns_404_when_not_found(client: AsyncClient, as_dpo):
    set_db(_empty_session())
    resp = await client.get("/api/v1/tables/nonexistent/pii-report")
    assert resp.status_code == 404


async def test_pii_report_requires_auth(client: AsyncClient):
    resp = await client.get("/api/v1/tables/tbl-001/pii-report")
    assert resp.status_code == 401


# ─── POST /api/v1/tables/{id}/remediate ──────────────────────────────────────


async def test_remediate_viewer_gets_403(client: AsyncClient, as_viewer):
    resp = await client.post(
        "/api/v1/tables/tbl-001/remediate", json={"action": "quarantine"}
    )
    assert resp.status_code == 403


async def test_remediate_invalid_action_returns_422(client: AsyncClient, as_dpo):
    set_db(_empty_session())
    resp = await client.post(
        "/api/v1/tables/tbl-001/remediate", json={"action": "delete_everything"}
    )
    assert resp.status_code == 422


async def test_remediate_unknown_table_returns_404(client: AsyncClient, as_dpo):
    set_db(_empty_session())
    resp = await client.post(
        "/api/v1/tables/nonexistent/remediate", json={"action": "quarantine"}
    )
    assert resp.status_code == 404
