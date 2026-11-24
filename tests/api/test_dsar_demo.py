"""DSAR search (GDPR Art. 15) and the demo seed endpoint."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import ALGORITHM, SECRET_KEY
from api.main import app
from api.routers import demo, dsar
from tests.api._fakes import DPO, VIEWER, FakeSession, Result, login_as, reset, row, use_session


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _overrides():
    login_as(DPO)
    yield
    reset()


def _match(table_id="t1", status="flagged"):
    return row(
        source_name="prod.users",
        table_id=table_id,
        data_source_type="s3",
        owner_email="o@company.com",
        estimated_row_count=1000,
        last_scanned=datetime(2026, 1, 1, tzinfo=UTC),
        status=status,
        confidence=0.9712,
    )


# ─── DSAR ─────────────────────────────────────────────────────────────────────


async def test_dsar_search_returns_matches_and_never_stores_the_identifier(client):
    session = use_session(
        FakeSession(Result(scalar=12), Result([_match("t1"), _match("t2", "quarantined")]))
    )
    secret_value = "Alice.Smith@Example.com"
    resp = await client.post(
        "/api/v1/dsar/search",
        json={"identifier_type": "email", "identifier_value": secret_value},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["pii_category_searched"] == "EMAIL"
    assert body["tables_matched"] == 2
    assert body["matches"][0]["confidence"] == 0.97
    assert body["matches"][1]["status"] == "quarantined"

    # Privacy: the raw identifier appears in no statement or parameter.
    assert secret_value.lower() not in repr(session.calls).lower()

    _, params = session.sql_containing("INSERT INTO dsar_searches")[0]
    expected = hmac.new(dsar._HASH_KEY, b"alice.smith@example.com", hashlib.sha256).hexdigest()
    assert params["hash"] == expected
    # a plain SHA-256 would be reversible by dictionary attack
    assert params["hash"] != hashlib.sha256(secret_value.encode()).hexdigest()
    assert params["searched"] == 12  # tables searched, not tables matched
    assert params["matched"] == 2
    assert session.audit_details()[0]["tables_matched"] == 2
    assert session.committed


async def test_dsar_hash_normalizes_case_and_whitespace():
    assert dsar._identifier_hash("  A@B.com ") == dsar._identifier_hash("a@b.com")


@pytest.mark.parametrize(
    "body",
    [
        {"identifier_type": "ssn", "identifier_value": "x"},  # unsupported type
        {"identifier_type": "email", "identifier_value": ""},  # empty value
        {"identifier_type": "email"},  # missing value
    ],
)
async def test_dsar_search_rejects_invalid_requests(client, body):
    use_session(FakeSession())
    resp = await client.post("/api/v1/dsar/search", json=body)
    assert resp.status_code == 422


async def test_dsar_requires_dpo_or_admin(client):
    login_as(VIEWER)
    use_session(FakeSession())
    resp = await client.post(
        "/api/v1/dsar/search", json={"identifier_type": "phone", "identifier_value": "1"}
    )
    assert resp.status_code == 403


async def test_dsar_search_history_lists_recent_searches(client):
    use_session(
        FakeSession(
            Result(
                [
                    row(
                        search_id="5f0c",
                        initiated_by="dpo@company.com",
                        identifier_type="national_id",
                        tables_searched_count=10,
                        tables_matched_count=1,
                        created_at=datetime(2026, 2, 1, tzinfo=UTC),
                    )
                ]
            )
        )
    )
    resp = await client.get("/api/v1/dsar/searches")
    searches = resp.json()["searches"]
    assert searches[0]["identifier_type"] == "national_id"
    assert searches[0]["created_at"].startswith("2026-02-01")


# ─── Demo seed ────────────────────────────────────────────────────────────────


async def test_demo_seed_is_forbidden_unless_demo_mode(client, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    session = use_session(FakeSession())
    resp = await client.post("/api/v1/demo/seed")

    assert resp.status_code == 403
    assert session.calls == []  # nothing is deleted or written


async def test_demo_seed_inserts_dataset_in_batches_and_returns_dpo_token(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    session = use_session(
        FakeSession(Result(scalar="CHECK (status IN ('flagged', 'quarantined'))"))
    )
    resp = await client.post("/api/v1/demo/seed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["quarantine_status_supported"] is True
    claims = jwt.decode(body["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    assert claims["role"] == "dpo"

    def batch(fragment):
        ((_, params),) = session.sql_containing(fragment)  # exactly one round trip
        return params

    events = batch("INSERT INTO scanner_events")
    findings = batch("INSERT INTO pii_findings")
    assert len(events) == len(demo._DEMO_TABLES)
    assert len(findings) == sum(len(t["findings"]) for t in demo._DEMO_TABLES)
    assert len(batch("INSERT INTO quarantine_manifest")) == len(demo._QUARANTINE_MANIFEST)
    assert {f["status"] for f in findings} == {"flagged", "quarantined", "remediated"}
    # timestamps are relative to the request, never in the future
    assert all(e["ts"] <= datetime.now(UTC) for e in events)
    # previous demo rows are cleared first (audit_log is append-only: kept)
    assert session.sql_containing("DELETE FROM pii_findings")
    assert not session.sql_containing("DELETE FROM audit_log")
    assert session.committed


async def test_demo_seed_falls_back_when_quarantined_status_is_not_allowed(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    session = use_session(FakeSession(Result(scalar="CHECK (status IN ('flagged', 'clean'))")))
    resp = await client.post("/api/v1/demo/seed")

    assert resp.json()["quarantine_status_supported"] is False
    ((_, findings),) = session.sql_containing("INSERT INTO pii_findings")
    assert "quarantined" not in {f["status"] for f in findings}
