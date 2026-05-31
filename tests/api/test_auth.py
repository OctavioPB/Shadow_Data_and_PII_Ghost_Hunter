"""
Unit tests for JWT auth — [S6-06].

DB calls are mocked via per-test dependency overrides. Tests verify token
issuance, invalid-credential rejection, and role-based access enforcement.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import get_current_user
from api.db import get_db
from api.main import app


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _async_db_empty():
    """AsyncSession mock whose execute() returns no rows and scalar=None."""
    result = MagicMock()
    result.fetchone.return_value = None
    result.fetchall.return_value = []
    result.scalar.return_value = None
    session = AsyncMock()
    session.execute.return_value = result
    session.commit = AsyncMock()
    return session


@pytest.fixture(autouse=True)
def clear_overrides():
    """Ensure no overrides bleed between tests."""
    yield
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


# ─── Token issuance ───────────────────────────────────────────────────────────


async def test_login_returns_token(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/token",
        data={"username": "admin@company.com", "password": "admin"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["role"] == "admin"


async def test_login_invalid_password_returns_401(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/token",
        data={"username": "admin@company.com", "password": "wrong"},
    )
    assert resp.status_code == 401


async def test_login_unknown_user_returns_401(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/token",
        data={"username": "ghost@company.com", "password": "anything"},
    )
    assert resp.status_code == 401


async def test_me_returns_user_info(client: AsyncClient):
    # First obtain a real token
    token_resp = await client.post(
        "/api/v1/auth/token",
        data={"username": "dpo@company.com", "password": "dpo"},
    )
    token = token_resp.json()["access_token"]

    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "dpo@company.com"
    assert body["role"] == "dpo"


async def test_me_without_token_returns_401(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


# ─── Role-based access ────────────────────────────────────────────────────────


async def test_remediate_viewer_gets_403(client: AsyncClient):
    """viewer role must receive 403 on the remediate endpoint."""
    token_resp = await client.post(
        "/api/v1/auth/token",
        data={"username": "viewer@company.com", "password": "viewer"},
    )
    token = token_resp.json()["access_token"]

    resp = await client.post(
        "/api/v1/tables/tbl-001/remediate",
        json={"action": "quarantine"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_dpo_passes_auth_on_remediate(client: AsyncClient):
    """DPO role must not receive 403 — DB returns 404 because table absent."""
    token_resp = await client.post(
        "/api/v1/auth/token",
        data={"username": "dpo@company.com", "password": "dpo"},
    )
    token = token_resp.json()["access_token"]

    async def _db():
        yield _async_db_empty()

    app.dependency_overrides[get_db] = _db

    resp = await client.post(
        "/api/v1/tables/nonexistent/remediate",
        json={"action": "quarantine"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # 404 means auth passed — DPO is authorised, table was just not found
    assert resp.status_code == 404
