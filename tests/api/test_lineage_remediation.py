"""Risk inventory filters, PII report, manual remediation and lineage endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
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


NOW = datetime(2026, 3, 1, tzinfo=UTC)


# ─── /risks filters ───────────────────────────────────────────────────────────


async def test_risks_filters_are_parameterized_and_status_is_applied(client):
    risk = row(
        table_id="t1",
        source_name="staging.users",
        data_source_type="s3",
        owner_email=None,
        pii_categories=["EMAIL"],
        max_confidence=0.93,
        flagged_column_count=2,
        last_scanned=NOW,
        status="flagged",
    )
    session = use_session(FakeSession(Result([risk]), Result(scalar=41)))
    resp = await client.get(
        "/api/v1/risks",
        params={
            "status": "flagged",
            "pii_category": "EMAIL",
            "source": "staging",
            "date_from": "2026-01-01T00:00:00Z",
            "date_to": "2026-12-31T00:00:00Z",
            "page": 3,
            "size": 10,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 41 and body["pages"] == 5
    assert body["items"][0]["pii_categories"] == ["EMAIL"]

    data_sql, data_params = session.calls[0]
    count_sql, count_params = session.calls[1]
    # status used to be silently ignored; it now filters the computed table status
    assert "WHERE status = :status" in data_sql and "WHERE status = :status" in count_sql
    assert "bool_or(pf.pii_category = :pii_category)" in data_sql
    assert data_params["source"] == "%staging%"
    assert data_params["limit"] == 10 and data_params["offset"] == 20
    assert "limit" not in count_params  # total counts all pages


async def test_risks_reject_unknown_status(client):
    resp = await client.get("/api/v1/risks", params={"status": "deleted"})
    assert resp.status_code == 422


# ─── PII report ───────────────────────────────────────────────────────────────


async def test_pii_report_lists_flagged_columns(client):
    use_session(
        FakeSession(
            Result(
                [
                    row(
                        source_name="prod.u",
                        data_source_type="s3",
                        owner_email=None,
                        last_scanned=NOW,
                    )
                ]
            ),
            Result(
                [
                    row(
                        column_name="email",
                        pii_category="EMAIL",
                        confidence=0.97,
                        status="flagged",
                        sample_count=100,
                    ),
                    row(
                        column_name="ssn",
                        pii_category="SSN",
                        confidence=0.9,
                        status="flagged",
                        sample_count=None,
                    ),
                ]
            ),
        )
    )
    body = (await client.get("/api/v1/tables/t1/pii-report")).json()

    assert [c["column_name"] for c in body["flagged_columns"]] == ["email", "ssn"]
    assert body["last_scanned"].startswith("2026-03-01")


# ─── Manual remediation ───────────────────────────────────────────────────────


async def test_remediate_audits_request_with_escaped_notes(client):
    session = use_session(FakeSession(Result([row(x=1)])))
    notes = 'contains "quotes", a \\ backslash and {"injected": true}'
    resp = await client.post(
        "/api/v1/tables/t1/remediate", json={"action": "anonymize", "notes": notes}
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    _, params = session.sql_containing("INSERT INTO audit_log")[0]
    assert params["event_type"] == "manual_anonymize_requested"
    assert params["actor"] == DPO["email"]
    assert session.audit_details() == [
        {"action": "anonymize", "notes": notes, "source": "dashboard"}
    ]


async def test_remediate_unknown_table_is_404(client):
    use_session(FakeSession(Result([])))
    resp = await client.post("/api/v1/tables/nope/remediate", json={"action": "quarantine"})
    assert resp.status_code == 404


async def test_remediate_requires_dpo_or_admin(client):
    login_as(VIEWER)
    use_session(FakeSession())
    resp = await client.post("/api/v1/tables/t1/remediate", json={"action": "quarantine"})
    assert resp.status_code == 403


# ─── Lineage ──────────────────────────────────────────────────────────────────


async def test_get_lineage_returns_parents_and_children(client):
    edge = {"source_name": "prod.u", "confidence": 0.6, "inference_method": "path_heuristic"}
    use_session(
        FakeSession(
            Result([row(table_id="p1", status="flagged", **edge)]),
            Result([row(table_id="c1", status=None, **edge)]),
        )
    )
    body = (await client.get("/api/v1/tables/t1/lineage")).json()

    assert body["parents"][0]["table_id"] == "p1"
    assert body["children"][0]["status"] == "unknown"  # no flagged findings


async def test_infer_lineage_links_copies_and_counts_only_new_edges(client):
    session = use_session(
        FakeSession(
            Result([row(source_name="prod.customers")]),  # this table: prod-like
            Result(
                [
                    row(table_id="t-staging", source_name="staging.customers_copy"),
                    row(table_id="t-prod2", source_name="prod.orders"),  # prod-prod: no edge
                    row(table_id="t-dev", source_name="dev.customers_dump"),
                ]
            ),
            Result([row(id="new-edge")]),  # RETURNING -> inserted
            Result([]),  # ON CONFLICT DO NOTHING -> already existed
        )
    )
    resp = await client.post("/api/v1/tables/t-prod/lineage/infer")

    assert resp.json() == {"created": 1, "method": "path_heuristic"}
    inserts = session.sql_containing("INSERT INTO lineage_edges")
    assert [p for _, p in inserts] == [
        {"p": "t-prod", "c": "t-staging"},
        {"p": "t-prod", "c": "t-dev"},
    ]
    assert session.audit_details() == [{"edges_created": 1, "method": "path_heuristic"}]


async def test_infer_lineage_treats_a_copy_as_the_child(client):
    session = use_session(
        FakeSession(
            Result([row(source_name="backup.customers_2024")]),
            Result([row(table_id="t-prod", source_name="prod.customers")]),
            Result([row(id="e")]),
        )
    )
    await client.post("/api/v1/tables/t-backup/lineage/infer")
    assert session.sql_containing("INSERT INTO lineage_edges")[0][1] == {
        "p": "t-prod",
        "c": "t-backup",
    }


async def test_infer_lineage_unknown_table_is_404(client):
    use_session(FakeSession(Result([])))
    resp = await client.post("/api/v1/tables/nope/lineage/infer")
    assert resp.status_code == 404


async def test_cascade_review_queues_confirmed_children_in_one_batch(client):
    session = use_session(FakeSession(Result([row(child_table_id="c1"), row(child_table_id="c2")])))
    body = (await client.post("/api/v1/tables/t1/lineage/cascade-review")).json()

    assert body == {"queued": 2, "child_table_ids": ["c1", "c2"]}
    ((_, params),) = session.sql_containing("INSERT INTO audit_log")
    assert [p["cid"] for p in params] == ["c1", "c2"]
    assert session.audit_details() == [{"triggered_by": "t1"}] * 2


async def test_cascade_review_without_children_writes_nothing(client):
    session = use_session(FakeSession(Result([])))
    body = (await client.post("/api/v1/tables/t1/lineage/cascade-review")).json()

    assert body["queued"] == 0
    assert not session.sql_containing("INSERT INTO audit_log")
