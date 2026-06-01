"""
Compliance endpoints: ROPA, Trends, Forecast, Risk Exposure.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, require_role
from api.db import get_db

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])

_LEGAL_BASES = [
    "Contract performance",
    "Legal obligation",
    "Legitimate interest",
    "Consent",
    "Vital interests",
    "Public task",
]


# ── ROPA ──────────────────────────────────────────────────────────────────────

async def _ropa_rows(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(text("""
        SELECT
            se.source_name,
            se.data_source_type,
            se.owner_email,
            array_agg(DISTINCT pf.pii_category)  AS pii_categories,
            min(se.created_at)                   AS first_detected_at,
            max(pf.created_at)                   AS last_scanned_at,
            CASE
                WHEN bool_and(pf.status = 'remediated')                        THEN 'remediated'
                WHEN count(*) FILTER (WHERE pf.status = 'quarantined') > 0     THEN 'quarantined'
                WHEN count(*) FILTER (WHERE pf.status = 'flagged')     > 0     THEN 'flagged'
                ELSE 'classified'
            END AS current_status,
            ra.purpose,
            ra.legal_basis,
            coalesce(ra.cross_border_transfer, false) AS cross_border_transfer
        FROM scanner_events se
        JOIN pii_findings pf
            ON pf.scanner_event_id = se.id AND pf.flagged = true
        LEFT JOIN LATERAL (
            SELECT purpose, legal_basis, cross_border_transfer
            FROM ropa_annotations
            WHERE source_name = se.source_name
            ORDER BY annotated_at DESC
            LIMIT 1
        ) ra ON true
        GROUP BY se.source_name, se.data_source_type, se.owner_email,
                 ra.purpose, ra.legal_basis, ra.cross_border_transfer
        ORDER BY min(se.created_at) DESC
    """))
    return [
        {
            "source_name": r.source_name,
            "data_source_type": r.data_source_type,
            "owner_email": r.owner_email,
            "pii_categories": list(r.pii_categories or []),
            "first_detected_at": r.first_detected_at.isoformat()
                if r.first_detected_at else None,
            "last_scanned_at": r.last_scanned_at.isoformat()
                if r.last_scanned_at else None,
            "current_status": r.current_status,
            "purpose": r.purpose,
            "legal_basis": r.legal_basis,
            "cross_border_transfer": r.cross_border_transfer,
        }
        for r in result.fetchall()
    ]


@router.get("/ropa")
async def get_ropa(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict:
    entries = await _ropa_rows(db)
    incomplete = sum(1 for e in entries if not e["purpose"])
    return {
        "entries": entries,
        "total": len(entries),
        "incomplete_entries": incomplete,
        "legal_bases": _LEGAL_BASES,
    }


@router.get("/ropa/export.csv")
async def export_ropa_csv(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> StreamingResponse:
    entries = await _ropa_rows(db)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Data Source", "Type", "Owner",
        "PII Categories", "First Detected", "Last Scanned",
        "Status", "Processing Purpose", "Legal Basis",
        "Cross-Border Transfer",
    ])
    for e in entries:
        w.writerow([
            e["source_name"], e["data_source_type"], e["owner_email"] or "",
            ", ".join(e["pii_categories"]),
            e["first_detected_at"] or "", e["last_scanned_at"] or "",
            e["current_status"],
            e["purpose"] or "(not documented)",
            e["legal_basis"] or "(not documented)",
            "Yes" if e["cross_border_transfer"] else "No",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ropa_export.csv"},
    )


@router.patch("/ropa/{source_name}")
async def annotate_ropa(
    source_name: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("dpo", "admin")),
) -> dict:
    await db.execute(
        text("""
            INSERT INTO ropa_annotations
                (source_name, purpose, legal_basis, cross_border_transfer,
                 annotated_by)
            VALUES (:sn, :purpose, :lb, :cbt, :actor)
        """),
        {
            "sn": source_name,
            "purpose": body.get("purpose"),
            "lb": body.get("legal_basis"),
            "cbt": bool(body.get("cross_border_transfer", False)),
            "actor": user["email"],
        },
    )
    await db.execute(
        text("""
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES ('ropa_annotated', :sn, :actor, CAST(:d AS jsonb))
        """),
        {
            "sn": source_name,
            "actor": user["email"],
            "d": f'{{"source_name":"{source_name}","legal_basis":"{body.get("legal_basis","")}"}}',
        },
    )
    await db.commit()
    return {"status": "ok", "source_name": source_name}


# ── Trends ────────────────────────────────────────────────────────────────────

@router.get("/trends")
async def compliance_trends(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict:
    rows = await db.execute(text("""
        WITH weeks AS (
            SELECT generate_series(
                date_trunc('week', now() - interval '84 days'),
                date_trunc('week', now()),
                interval '1 week'
            ) AS week_start
        ),
        by_week AS (
            SELECT
                date_trunc('week', created_at)                                         AS week_start,
                count(DISTINCT table_id) FILTER (WHERE flagged = true)                 AS new_flagged,
                count(DISTINCT table_id)
                    FILTER (WHERE status IN ('remediated', 'quarantined'))              AS new_remediated
            FROM pii_findings
            WHERE created_at >= now() - interval '84 days'
            GROUP BY 1
        )
        SELECT
            w.week_start,
            coalesce(b.new_flagged, 0)    AS new_flagged,
            coalesce(b.new_remediated, 0) AS new_remediated
        FROM weeks w
        LEFT JOIN by_week b ON b.week_start = w.week_start
        ORDER BY w.week_start
    """))

    cum_flagged = 0
    cum_remediated = 0
    trend = []
    for r in rows.fetchall():
        cum_flagged += r.new_flagged
        cum_remediated += r.new_remediated
        score = (
            round((cum_remediated / cum_flagged) * 100, 1)
            if cum_flagged > 0 else 100.0
        )
        trend.append({
            "week_start": r.week_start.isoformat(),
            "new_flagged": r.new_flagged,
            "new_remediated": r.new_remediated,
            "cumulative_flagged": cum_flagged,
            "cumulative_remediated": cum_remediated,
            "compliance_score": score,
        })

    ttr = await db.execute(text("""
        SELECT round(avg(
            extract(epoch from (r.timestamp - d.timestamp)) / 86400
        )::numeric, 1) AS avg_days
        FROM audit_log d
        JOIN audit_log r ON r.table_id = d.table_id
        WHERE d.event_type = 'pii_detected'
          AND r.event_type IN ('anonymization_completed', 'quarantine_completed')
          AND d.timestamp >= now() - interval '84 days'
    """))
    ttr_row = ttr.fetchone()

    return {
        "trend": trend,
        "avg_ttr_days": float(ttr_row.avg_days) if ttr_row and ttr_row.avg_days else None,
    }


# ── Forecast ──────────────────────────────────────────────────────────────────

@router.get("/forecast")
async def compliance_forecast(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict:
    totals = await db.execute(text("""
        SELECT
            count(DISTINCT table_id) FILTER (WHERE flagged = true)                     AS total_flagged,
            count(DISTINCT table_id)
                FILTER (WHERE status IN ('remediated', 'quarantined'))                 AS total_remediated
        FROM pii_findings
    """))
    row = totals.fetchone()
    total_flagged = int(row.total_flagged or 0)
    total_remediated = int(row.total_remediated or 0)
    pending = total_flagged - total_remediated
    current_score = (
        round((total_remediated / total_flagged) * 100, 1) if total_flagged > 0 else 100.0
    )

    vel = await db.execute(text("""
        SELECT count(*) AS cnt
        FROM audit_log
        WHERE event_type IN ('anonymization_completed', 'quarantine_completed')
          AND timestamp >= now() - interval '28 days'
    """))
    vel_row = vel.fetchone()
    per_week = round(int(vel_row.cnt or 0) / 4.0, 1)

    projected_30 = min(total_remediated + int(per_week * 4), total_flagged)
    score_30 = (
        round((projected_30 / total_flagged) * 100, 1) if total_flagged > 0 else 100.0
    )
    days_to_full = (
        int(round(pending / (per_week / 7)))
        if per_week > 0 and pending > 0 else None
    )

    return {
        "current_score": current_score,
        "projected_score_30d": score_30,
        "remediations_per_week": per_week,
        "total_pending": pending,
        "days_to_full_compliance": days_to_full,
    }


# ── Risk exposure ─────────────────────────────────────────────────────────────

@router.get("/risk-exposure")
async def risk_exposure(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict:
    result = await db.execute(text("""
        SELECT coalesce(sum(se.estimated_row_count), 0) AS total_records
        FROM pii_findings pf
        JOIN scanner_events se ON se.id = pf.scanner_event_id
        WHERE pf.flagged = true
          AND pf.status NOT IN ('remediated', 'quarantined')
    """))
    row = result.fetchone()
    total = int(row.total_records or 0)
    return {
        "total_exposed_records": total,
        "estimated_fine_low_eur": int(total * 0.004),
        "estimated_fine_high_eur": int(total * 0.020),
        "methodology": (
            "Derived from median GDPR fines per affected individual "
            "(€0.004 low, €0.020 high — published DPA enforcement data). "
            "For internal risk planning only, not a legal opinion."
        ),
    }
