"""
Unit tests for the audit log endpoint — [S6-04].
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import get_current_user
from api.db import get_db
from api.main import app

_AUDITOR = {"email": "auditor@company.com", "role": "auditor", "name": "Auditor"}


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_overrides():
    app.dependency_overrides[get_current_user] = lambda: _AUDITOR
    yield
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def _empty_session():
    result = MagicMock()
    result.fetchall.return_value = []
    result.fetchone.return_value = None
    result.scalar.return_value = 0
    session = AsyncMock()
    session.execute.return_value = result
    return session


def set_db(session=None):
    s = session or _empty_session()

    async def _db():
        yield s

    app.dependency_overrides[get_db] = _db


# ─── Tests ────────────────────────────────────────────────────────────────────


async def test_audit_log_returns_200(client: AsyncClient):
    set_db()
    resp = await client.get("/api/v1/audit-log")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body


async def test_audit_log_empty_returns_zero_total(client: AsyncClient):
    set_db()
    resp = await client.get("/api/v1/audit-log")
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_audit_log_accepts_filter_params(client: AsyncClient):
    set_db()
    resp = await client.get(
        "/api/v1/audit-log?event_type=anonymization_completed&actor=system&page=1&size=10"
    )
    assert resp.status_code == 200


async def test_audit_log_requires_auth(client: AsyncClient):
    app.dependency_overrides.pop(get_current_user, None)
    resp = await client.get("/api/v1/audit-log")
    assert resp.status_code == 401


async def test_audit_log_export_returns_csv(client: AsyncClient):
    set_db()
    resp = await client.get("/api/v1/audit-log/export")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    assert "audit_log.csv" in resp.headers.get("content-disposition", "")


async def test_audit_log_page_size_reflected(client: AsyncClient):
    set_db()
    resp = await client.get("/api/v1/audit-log?page=3&size=25")
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 3
    assert body["size"] == 25
