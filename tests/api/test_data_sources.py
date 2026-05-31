"""
Unit tests for data-sources endpoint — [S7-05]

Covers GET /api/v1/data-sources with mocked DB.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import get_current_user
from api.db import get_db
from api.main import app

# ─── Helpers ──────────────────────────────────────────────────────────────────

_VIEWER = {"email": "viewer@company.com", "role": "viewer", "name": "Viewer"}


def _session_with_rows(rows: list):
    result = MagicMock()
    result.fetchall.return_value = rows
    result.fetchone.return_value = None
    result.scalar.return_value = 0
    session = AsyncMock()
    session.execute.return_value = result
    return session


def _empty_session():
    return _session_with_rows([])


def set_db(session=None):
    s = session or _empty_session()

    async def _db():
        yield s

    app.dependency_overrides[get_db] = _db


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_overrides():
    app.dependency_overrides[get_current_user] = lambda: _VIEWER
    yield
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


# ─── Tests ────────────────────────────────────────────────────────────────────


async def test_data_sources_empty_returns_200(client: AsyncClient):
    """Empty DB returns a valid DataSourcesResponse with no items."""
    set_db()
    resp = await client.get("/api/v1/data-sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_data_sources_schema_shape(client: AsyncClient):
    """Response envelope must have items list and total int."""
    set_db()
    resp = await client.get("/api/v1/data-sources")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "total" in body
    assert isinstance(body["items"], list)
    assert isinstance(body["total"], int)


async def test_data_sources_with_one_source(client: AsyncClient):
    """A single mocked row is serialised into the items list correctly."""
    row = MagicMock()
    row.source_name = "prod.customer_pii_backup"
    row.data_source_type = "s3"
    row.bucket = "data-lake-prod"
    row.table_count = 3
    row.flagged_count = 2
    row.max_confidence = 0.95
    row.pii_categories = ["EMAIL", "SSN"]

    set_db(_session_with_rows([row]))
    resp = await client.get("/api/v1/data-sources")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 1
    item = body["items"][0]
    assert item["source_name"] == "prod.customer_pii_backup"
    assert item["data_source_type"] == "s3"
    assert item["bucket"] == "data-lake-prod"
    assert item["table_count"] == 3
    assert item["flagged_count"] == 2
    assert item["max_confidence"] == pytest.approx(0.95, abs=0.01)
    assert set(item["pii_categories"]) == {"EMAIL", "SSN"}


async def test_data_sources_multiple_sources(client: AsyncClient):
    """Multiple rows produce matching total and item count."""
    rows = []
    for i in range(4):
        r = MagicMock()
        r.source_name = f"source-{i}"
        r.data_source_type = "s3"
        r.bucket = f"bucket-{i}"
        r.table_count = i + 1
        r.flagged_count = i
        r.max_confidence = 0.80 + i * 0.04
        r.pii_categories = ["EMAIL"]
        rows.append(r)

    set_db(_session_with_rows(rows))
    resp = await client.get("/api/v1/data-sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    assert len(body["items"]) == 4


async def test_data_sources_null_pii_categories_defaults_to_empty_list(client: AsyncClient):
    """When pii_categories is NULL in DB, the serialiser must return []."""
    row = MagicMock()
    row.source_name = "staging.temp"
    row.data_source_type = "redshift"
    row.bucket = None
    row.table_count = 1
    row.flagged_count = 0
    row.max_confidence = None
    row.pii_categories = None  # NULL from LEFT JOIN with no findings

    set_db(_session_with_rows([row]))
    resp = await client.get("/api/v1/data-sources")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["pii_categories"] == []
    assert item["max_confidence"] == pytest.approx(0.0, abs=0.001)


async def test_data_sources_null_bucket_returned_as_none(client: AsyncClient):
    """Sources without a bucket (e.g. Redshift) return bucket=null in JSON."""
    row = MagicMock()
    row.source_name = "analytics.dw"
    row.data_source_type = "redshift"
    row.bucket = None
    row.table_count = 5
    row.flagged_count = 1
    row.max_confidence = 0.88
    row.pii_categories = ["PHONE"]

    set_db(_session_with_rows([row]))
    resp = await client.get("/api/v1/data-sources")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["bucket"] is None


async def test_data_sources_requires_auth(client: AsyncClient):
    """Unauthenticated request must return 401."""
    app.dependency_overrides.pop(get_current_user, None)
    set_db()
    resp = await client.get("/api/v1/data-sources")
    assert resp.status_code == 401


async def test_data_sources_viewer_role_allowed(client: AsyncClient):
    """Viewer role (lowest privilege) must be able to read data sources."""
    app.dependency_overrides[get_current_user] = lambda: _VIEWER
    set_db()
    resp = await client.get("/api/v1/data-sources")
    assert resp.status_code == 200


async def test_data_sources_response_has_security_headers(client: AsyncClient):
    """SecurityHeadersMiddleware must be active on this endpoint."""
    set_db()
    resp = await client.get("/api/v1/data-sources")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Cache-Control") == "no-store"


async def test_data_sources_no_raw_values_in_response(client: AsyncClient):
    """Response items must not expose raw column values — only metadata."""
    row = MagicMock()
    row.source_name = "prod.users"
    row.data_source_type = "s3"
    row.bucket = "bucket"
    row.table_count = 10
    row.flagged_count = 3
    row.max_confidence = 0.91
    row.pii_categories = ["SSN"]

    set_db(_session_with_rows([row]))
    resp = await client.get("/api/v1/data-sources")
    item = resp.json()["items"][0]

    # These keys must never appear in the data-sources response
    assert "values" not in item
    assert "sample" not in item
    assert "rows" not in item
