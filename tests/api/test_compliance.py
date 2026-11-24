"""Compliance endpoints: ROPA, trends, forecast, risk exposure."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

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


def _ropa_row(**overrides):
    base = {
        "source_name": "prod.customers_backup",
        "data_source_type": "s3",
        "owner_email": "owner@company.com",
        "pii_categories": ["EMAIL", "SSN"],
        "first_detected_at": datetime(2026, 1, 1, tzinfo=UTC),
        "last_scanned_at": datetime(2026, 1, 2, tzinfo=UTC),
        "current_status": "flagged",
        "purpose": None,
        "legal_basis": None,
        "cross_border_transfer": False,
    }
    return row(**{**base, **overrides})


# ─── ROPA ─────────────────────────────────────────────────────────────────────


async def test_ropa_counts_entries_without_documented_purpose(client):
    use_session(FakeSession(Result([_ropa_row(), _ropa_row(source_name="b", purpose="Billing")])))
    resp = await client.get("/api/v1/compliance/ropa")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert body["incomplete_entries"] == 1
    assert "Consent" in body["legal_bases"]
    assert body["entries"][0]["first_detected_at"].startswith("2026-01-01")


async def test_ropa_csv_marks_undocumented_fields_and_neutralizes_formulas(client):
    use_session(
        FakeSession(
            Result(
                [
                    _ropa_row(source_name='=HYPERLINK("http://evil")', owner_email=None),
                    _ropa_row(
                        source_name="ok",
                        purpose="Billing",
                        legal_basis="Contract performance",
                        cross_border_transfer=True,
                        first_detected_at=None,
                    ),
                ]
            )
        )
    )
    resp = await client.get("/api/v1/compliance/ropa/export.csv")

    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][0] == "Data Source"
    assert rows[1][0].startswith("'=")  # formula neutralized
    assert rows[1][7] == "(not documented)"
    assert rows[2][8] == "Contract performance"
    assert rows[2][9] == "Yes"


async def test_ropa_annotation_is_stored_and_audited(client):
    session = use_session(FakeSession())
    resp = await client.patch(
        "/api/v1/compliance/ropa/prod.customers_backup",
        json={
            "purpose": 'Backups "legacy"',
            "legal_basis": "Legal obligation",
            "cross_border_transfer": True,
        },
    )

    assert resp.status_code == 200
    _, params = session.sql_containing("INSERT INTO ropa_annotations")[0]
    assert params["purpose"] == 'Backups "legacy"'
    assert params["lb"] == "Legal obligation"
    assert params["cbt"] is True
    assert params["actor"] == DPO["email"]
    # quotes in user input must not break the JSON audit payload
    assert session.audit_details() == [
        {"source_name": "prod.customers_backup", "legal_basis": "Legal obligation"}
    ]
    assert session.committed


async def test_ropa_annotation_rejects_unknown_legal_basis(client):
    use_session(FakeSession())
    resp = await client.patch(
        "/api/v1/compliance/ropa/x", json={"legal_basis": "Because I said so"}
    )
    assert resp.status_code == 422


async def test_ropa_annotation_treats_empty_legal_basis_as_none(client):
    session = use_session(FakeSession())
    resp = await client.patch("/api/v1/compliance/ropa/x", json={"legal_basis": ""})

    assert resp.status_code == 200
    assert session.sql_containing("INSERT INTO ropa_annotations")[0][1]["lb"] is None


async def test_ropa_annotation_requires_dpo_or_admin(client):
    login_as(VIEWER)
    use_session(FakeSession())
    resp = await client.patch("/api/v1/compliance/ropa/x", json={"purpose": "p"})
    assert resp.status_code == 403


# ─── Trends ───────────────────────────────────────────────────────────────────


async def test_trends_accumulate_weekly_counts_into_a_score(client):
    week = datetime(2026, 1, 5, tzinfo=UTC)
    use_session(
        FakeSession(
            Result(
                [
                    row(week_start=week, new_flagged=0, new_remediated=0),
                    row(week_start=week + timedelta(weeks=1), new_flagged=4, new_remediated=1),
                    row(week_start=week + timedelta(weeks=2), new_flagged=0, new_remediated=1),
                ]
            ),
            Result([row(avg_days=2.5)]),
        )
    )
    resp = await client.get("/api/v1/compliance/trends")

    assert resp.status_code == 200
    trend = resp.json()["trend"]
    assert [p["compliance_score"] for p in trend] == [100.0, 25.0, 50.0]
    assert trend[-1]["cumulative_flagged"] == 4
    assert trend[-1]["cumulative_remediated"] == 2
    assert resp.json()["avg_ttr_days"] == 2.5


async def test_trends_without_remediations_have_no_ttr(client):
    use_session(FakeSession(Result([]), Result([row(avg_days=None)])))
    resp = await client.get("/api/v1/compliance/trends")
    assert resp.json() == {"trend": [], "avg_ttr_days": None}


# ─── Forecast ─────────────────────────────────────────────────────────────────


async def test_forecast_projects_score_and_days_to_full_compliance(client):
    use_session(
        FakeSession(
            Result([row(total_flagged=20, total_remediated=4)]),
            Result([row(cnt=8)]),  # 8 tables in 28 days -> 2 per week
        )
    )
    resp = await client.get("/api/v1/compliance/forecast")

    body = resp.json()
    assert body["current_score"] == 20.0
    assert body["remediations_per_week"] == 2.0
    assert body["total_pending"] == 16
    assert body["projected_score_30d"] == 60.0  # 4 + 2/week * 4 weeks = 12 of 20
    assert body["days_to_full_compliance"] == 56  # 16 pending at 2/7 per day


async def test_forecast_projection_is_capped_at_full_compliance(client):
    use_session(
        FakeSession(Result([row(total_flagged=10, total_remediated=4)]), Result([row(cnt=8)]))
    )
    body = (await client.get("/api/v1/compliance/forecast")).json()
    assert body["projected_score_30d"] == 100.0  # 4 + 8 would exceed the 10 flagged


async def test_forecast_with_no_velocity_has_no_eta(client):
    use_session(
        FakeSession(Result([row(total_flagged=3, total_remediated=0)]), Result([row(cnt=0)]))
    )
    body = (await client.get("/api/v1/compliance/forecast")).json()
    assert body["days_to_full_compliance"] is None
    assert body["projected_score_30d"] == 0.0


async def test_forecast_never_reports_negative_pending(client):
    use_session(
        FakeSession(Result([row(total_flagged=2, total_remediated=5)]), Result([row(cnt=0)]))
    )
    body = (await client.get("/api/v1/compliance/forecast")).json()
    assert body["total_pending"] == 0


async def test_forecast_with_nothing_flagged_is_fully_compliant(client):
    use_session(
        FakeSession(Result([row(total_flagged=0, total_remediated=0)]), Result([row(cnt=None)]))
    )
    body = (await client.get("/api/v1/compliance/forecast")).json()
    assert body["current_score"] == 100.0
    assert body["projected_score_30d"] == 100.0


# ─── Risk exposure ────────────────────────────────────────────────────────────


async def test_risk_exposure_estimates_fine_range_from_exposed_records(client):
    session = use_session(FakeSession(Result([row(total_records=1_000_000)])))
    body = (await client.get("/api/v1/compliance/risk-exposure")).json()

    assert body["total_exposed_records"] == 1_000_000
    assert body["estimated_fine_low_eur"] == 4_000
    assert body["estimated_fine_high_eur"] == 20_000
    # rows counted once per table (EXISTS), not once per flagged column (JOIN)
    assert "EXISTS" in session.calls[0][0]
