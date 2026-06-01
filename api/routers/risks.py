from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, require_role
from api.db import get_db
from api.schemas.risks import (
    ColumnFinding,
    DataSource,
    DataSourcesResponse,
    PIIReport,
    RemediateRequest,
    RemediateResponse,
    RiskItem,
    RisksResponse,
    StatsSummary,
)

router = APIRouter(prefix="/api/v1", tags=["risks"])

_RISK_BASE_QUERY = """
    SELECT
        pf.table_id,
        se.source_name,
        se.data_source_type,
        se.owner_email,
        se.bucket,
        array_agg(DISTINCT pf.pii_category)  AS pii_categories,
        max(pf.confidence)                    AS max_confidence,
        count(*) FILTER (WHERE pf.flagged = true) AS flagged_column_count,
        max(pf.created_at)                    AS last_scanned,
        CASE
            WHEN count(*) FILTER (WHERE pf.status = 'quarantined') > 0 THEN 'quarantined'
            WHEN count(*) FILTER (WHERE pf.status = 'remediated')  > 0 THEN 'remediated'
            WHEN count(*) FILTER (WHERE pf.status = 'flagged')     > 0 THEN 'flagged'
            ELSE 'classified'
        END AS status
    FROM pii_findings pf
    JOIN scanner_events se ON pf.scanner_event_id = se.id
    WHERE pf.flagged = true
"""


@router.get("/risks", response_model=RisksResponse)
async def list_risks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    pii_category: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    source: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> RisksResponse:
    conditions = ["pf.flagged = true"]
    params: dict[str, Any] = {}

    if pii_category:
        conditions.append(":pii_category = ANY(array_agg(pf.pii_category))")

    if source:
        conditions.append("se.source_name ILIKE :source")
        params["source"] = f"%{source}%"

    if date_from:
        conditions.append("pf.created_at >= :date_from")
        params["date_from"] = date_from

    if date_to:
        conditions.append("pf.created_at <= :date_to")
        params["date_to"] = date_to

    where_clause = "WHERE " + " AND ".join(conditions)

    having_clause = ""
    if pii_category:
        having_clause = "HAVING :pii_category = ANY(array_agg(DISTINCT pf.pii_category))"
        params["pii_category"] = pii_category

    status_having = ""
    if status_filter:
        having_parts = [having_clause] if having_clause else []
        status_sql = {
            "quarantined": "count(*) FILTER (WHERE pf.status = 'quarantined') > 0",
            "remediated": "count(*) FILTER (WHERE pf.status = 'remediated') > 0",
            "flagged": "count(*) FILTER (WHERE pf.status = 'flagged') > 0",
        }.get(status_filter)
        if status_sql:
            having_parts.append(status_sql)
        if having_parts:
            having_clause = "HAVING " + " AND ".join(having_parts)

    data_query = text(f"""
        SELECT
            pf.table_id,
            se.source_name,
            se.data_source_type,
            se.owner_email,
            array_agg(DISTINCT pf.pii_category)            AS pii_categories,
            max(pf.confidence)                              AS max_confidence,
            count(*) FILTER (WHERE pf.flagged = true)         AS flagged_column_count,
            max(pf.created_at)                             AS last_scanned,
            CASE
                WHEN count(*) FILTER (WHERE pf.status = 'quarantined') > 0 THEN 'quarantined'
                WHEN count(*) FILTER (WHERE pf.status = 'remediated')  > 0 THEN 'remediated'
                WHEN count(*) FILTER (WHERE pf.status = 'flagged')     > 0 THEN 'flagged'
                ELSE 'classified'
            END AS status
        FROM pii_findings pf
        JOIN scanner_events se ON pf.scanner_event_id = se.id
        WHERE pf.flagged = true
        {"AND se.source_name ILIKE :source" if source else ""}
        {"AND pf.created_at >= :date_from" if date_from else ""}
        {"AND pf.created_at <= :date_to" if date_to else ""}
        GROUP BY pf.table_id, se.source_name, se.data_source_type, se.owner_email
        {"HAVING :pii_category = ANY(array_agg(DISTINCT pf.pii_category))" if pii_category else ""}
        ORDER BY max(pf.confidence) DESC
        LIMIT :limit OFFSET :offset
    """)

    count_query = text(f"""
        SELECT count(*) FROM (
            SELECT pf.table_id
            FROM pii_findings pf
            JOIN scanner_events se ON pf.scanner_event_id = se.id
            WHERE pf.flagged = true
            {"AND se.source_name ILIKE :source" if source else ""}
            {"AND pf.created_at >= :date_from" if date_from else ""}
            {"AND pf.created_at <= :date_to" if date_to else ""}
            GROUP BY pf.table_id
            {"HAVING :pii_category = ANY(array_agg(DISTINCT pf.pii_category))" if pii_category else ""}
        ) sub
    """)

    offset = (page - 1) * size
    params["limit"] = size
    params["offset"] = offset

    rows = (await db.execute(data_query, params)).fetchall()
    total = (await db.execute(count_query, params)).scalar() or 0

    items = [
        RiskItem(
            table_id=r.table_id,
            source_name=r.source_name,
            data_source_type=r.data_source_type,
            owner_email=r.owner_email,
            pii_categories=list(r.pii_categories) if r.pii_categories else [],
            max_confidence=float(r.max_confidence),
            flagged_column_count=int(r.flagged_column_count),
            last_scanned=r.last_scanned,
            status=r.status,
        )
        for r in rows
    ]

    return RisksResponse(
        items=items,
        total=int(total),
        page=page,
        size=size,
        pages=math.ceil(int(total) / size) if size else 1,
    )


@router.get("/stats/summary", response_model=StatsSummary)
async def stats_summary(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> StatsSummary:
    result = await db.execute(
        text("""
            SELECT
                count(DISTINCT pf.table_id) FILTER (WHERE pf.flagged = true)             AS total_flagged,
                count(DISTINCT pf.table_id) FILTER (WHERE pf.status IN ('remediated', 'quarantined'))
                                                                                       AS remediated,
                count(DISTINCT pf.table_id) FILTER (WHERE pf.flagged = true
                    AND pf.status NOT IN ('remediated', 'quarantined'))                AS pending_review
            FROM pii_findings pf
        """)
    )
    row = result.fetchone()
    total_flagged = int(row.total_flagged or 0)
    remediated = int(row.remediated or 0)
    pending_review = int(row.pending_review or 0)
    compliance_score = (
        round((remediated / total_flagged) * 100, 1) if total_flagged > 0 else 100.0
    )
    return StatsSummary(
        total_flagged=total_flagged,
        remediated=remediated,
        pending_review=pending_review,
        compliance_score=compliance_score,
    )


@router.get("/tables/{table_id}/pii-report", response_model=PIIReport)
async def pii_report(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> PIIReport:
    meta = await db.execute(
        text("""
            SELECT se.source_name, se.data_source_type, se.owner_email
            FROM pii_findings pf
            JOIN scanner_events se ON pf.scanner_event_id = se.id
            WHERE pf.table_id = :tid
            LIMIT 1
        """),
        {"tid": table_id},
    )
    meta_row = meta.fetchone()
    if not meta_row:
        raise HTTPException(status_code=404, detail="Table not found")

    findings = await db.execute(
        text("""
            SELECT
                pf.column_name,
                pf.pii_category,
                pf.confidence,
                pf.status,
                cs.sample_count
            FROM pii_findings pf
            LEFT JOIN column_samples cs
                ON cs.table_id = pf.table_id AND cs.column_name = pf.column_name
            WHERE pf.table_id = :tid AND pf.flagged = true
            ORDER BY pf.confidence DESC
        """),
        {"tid": table_id},
    )

    last_scanned_q = await db.execute(
        text("SELECT max(created_at) FROM pii_findings WHERE table_id = :tid"),
        {"tid": table_id},
    )

    columns = [
        ColumnFinding(
            column_name=r.column_name,
            pii_category=r.pii_category,
            confidence=float(r.confidence),
            sample_count=r.sample_count,
            status=r.status,
        )
        for r in findings.fetchall()
    ]

    return PIIReport(
        table_id=table_id,
        source_name=meta_row.source_name,
        data_source_type=meta_row.data_source_type,
        owner_email=meta_row.owner_email,
        flagged_columns=columns,
        last_scanned=last_scanned_q.scalar(),
    )


@router.post("/tables/{table_id}/remediate", response_model=RemediateResponse)
async def remediate(
    table_id: str,
    body: RemediateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("dpo", "admin")),
) -> RemediateResponse:
    check = await db.execute(
        text("SELECT 1 FROM pii_findings WHERE table_id = :tid LIMIT 1"),
        {"tid": table_id},
    )
    if not check.fetchone():
        raise HTTPException(status_code=404, detail="Table not found")

    await db.execute(
        text("""
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES (:event_type, :table_id, :actor, CAST(:details AS jsonb))
        """),
        {
            "event_type": f"manual_{body.action}_requested",
            "table_id": table_id,
            "actor": user["email"],
            "details": f'{{"action":"{body.action}","notes":"{body.notes or ""}","source":"dashboard"}}',
        },
    )
    await db.commit()

    return RemediateResponse(
        table_id=table_id,
        action=body.action,
        status="queued",
        message=f"Manual {body.action} queued for DPO review.",
    )


@router.get("/data-sources", response_model=DataSourcesResponse)
async def data_sources(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> DataSourcesResponse:
    rows = await db.execute(
        text("""
            SELECT
                se.source_name,
                se.data_source_type,
                se.bucket,
                count(DISTINCT pf.table_id)                        AS table_count,
                count(DISTINCT pf.table_id) FILTER (WHERE pf.flagged = true) AS flagged_count,
                max(pf.confidence)                                 AS max_confidence,
                array_agg(DISTINCT pf.pii_category)
                    FILTER (WHERE pf.flagged = true)                  AS pii_categories
            FROM scanner_events se
            LEFT JOIN pii_findings pf ON pf.scanner_event_id = se.id
            GROUP BY se.source_name, se.data_source_type, se.bucket
            ORDER BY flagged_count DESC, max_confidence DESC
        """)
    )

    items = [
        DataSource(
            source_name=r.source_name,
            data_source_type=r.data_source_type,
            bucket=r.bucket,
            region=None,
            table_count=int(r.table_count or 0),
            flagged_count=int(r.flagged_count or 0),
            max_confidence=float(r.max_confidence or 0.0),
            pii_categories=list(r.pii_categories) if r.pii_categories else [],
        )
        for r in rows.fetchall()
    ]

    return DataSourcesResponse(items=items, total=len(items))


# ── Lineage ───────────────────────────────────────────────────────────────────

@router.get("/tables/{table_id}/lineage")
async def get_lineage(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> dict:
    parents = await db.execute(
        text("""
            SELECT le.parent_table_id, se.source_name,
                   le.confidence, le.inference_method,
                   CASE
                     WHEN count(*) FILTER (WHERE pf.status = 'quarantined') > 0 THEN 'quarantined'
                     WHEN count(*) FILTER (WHERE pf.status = 'remediated')  > 0 THEN 'remediated'
                     WHEN count(*) FILTER (WHERE pf.status = 'flagged')     > 0 THEN 'flagged'
                     ELSE 'classified'
                   END AS status
            FROM lineage_edges le
            LEFT JOIN scanner_events se ON se.source_name = le.parent_table_id
            LEFT JOIN pii_findings pf
                ON pf.table_id = le.parent_table_id AND pf.flagged = true
            WHERE le.child_table_id = :tid
            GROUP BY le.parent_table_id, se.source_name,
                     le.confidence, le.inference_method
        """),
        {"tid": table_id},
    )
    children = await db.execute(
        text("""
            SELECT le.child_table_id, se.source_name,
                   le.confidence, le.inference_method,
                   CASE
                     WHEN count(*) FILTER (WHERE pf.status = 'quarantined') > 0 THEN 'quarantined'
                     WHEN count(*) FILTER (WHERE pf.status = 'remediated')  > 0 THEN 'remediated'
                     WHEN count(*) FILTER (WHERE pf.status = 'flagged')     > 0 THEN 'flagged'
                     ELSE 'classified'
                   END AS status
            FROM lineage_edges le
            LEFT JOIN scanner_events se ON se.source_name = le.child_table_id
            LEFT JOIN pii_findings pf
                ON pf.table_id = le.child_table_id AND pf.flagged = true
            WHERE le.parent_table_id = :tid
            GROUP BY le.child_table_id, se.source_name,
                     le.confidence, le.inference_method
        """),
        {"tid": table_id},
    )
    return {
        "table_id": table_id,
        "parents": [
            {
                "table_id": r.parent_table_id,
                "source_name": r.source_name,
                "confidence": round(float(r.confidence), 2),
                "inference_method": r.inference_method,
                "status": r.status or "unknown",
            }
            for r in parents.fetchall()
        ],
        "children": [
            {
                "table_id": r.child_table_id,
                "source_name": r.source_name,
                "confidence": round(float(r.confidence), 2),
                "inference_method": r.inference_method,
                "status": r.status or "unknown",
            }
            for r in children.fetchall()
        ],
    }


@router.post("/tables/{table_id}/lineage/infer")
async def infer_lineage(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("dpo", "admin")),
) -> dict:
    """Path-heuristic lineage: tables named backup/staging/dev/temp/copy/dump
    are treated as children of prod-like sources."""
    child_keywords = {
        "backup", "copy", "dump", "staging", "dev", "test",
        "temp", "archive", "export", "sample", "snapshot",
    }

    this = await db.execute(
        text("SELECT source_name FROM scanner_events WHERE id::text = :tid OR source_name = :tid LIMIT 1"),
        {"tid": table_id},
    )
    this_row = this.fetchone()
    if not this_row:
        return {"created": 0, "message": "Table not found"}

    this_source = this_row.source_name
    this_is_child = any(kw in this_source.lower() for kw in child_keywords)

    others = await db.execute(
        text("""
            SELECT DISTINCT pf.table_id, se.source_name
            FROM pii_findings pf
            JOIN scanner_events se ON se.id = pf.scanner_event_id
            WHERE pf.flagged = true AND pf.table_id != :tid
        """),
        {"tid": table_id},
    )

    created = 0
    for row in others.fetchall():
        other_is_child = any(kw in row.source_name.lower() for kw in child_keywords)

        if this_is_child and not other_is_child:
            parent_id, child_id = row.table_id, table_id
        elif not this_is_child and other_is_child:
            parent_id, child_id = table_id, row.table_id
        else:
            continue

        try:
            await db.execute(
                text("""
                    INSERT INTO lineage_edges
                        (parent_table_id, child_table_id, confidence, inference_method)
                    VALUES (:p, :c, 0.6, 'path_heuristic')
                    ON CONFLICT (parent_table_id, child_table_id) DO NOTHING
                """),
                {"p": parent_id, "c": child_id},
            )
            created += 1
        except Exception:
            await db.rollback()

    await db.execute(
        text("""
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES ('lineage_inferred', :tid, :actor, CAST(:d AS jsonb))
        """),
        {
            "tid": table_id,
            "actor": user["email"],
            "d": f'{{"edges_created":{created},"method":"path_heuristic"}}',
        },
    )
    await db.commit()
    return {"created": created, "method": "path_heuristic"}


@router.post("/tables/{table_id}/lineage/cascade-review")
async def cascade_review(
    table_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("dpo", "admin")),
) -> dict:
    children = await db.execute(
        text("""
            SELECT child_table_id FROM lineage_edges
            WHERE parent_table_id = :tid AND confidence >= 0.7
        """),
        {"tid": table_id},
    )
    child_ids = [r.child_table_id for r in children.fetchall()]
    if not child_ids:
        return {"queued": 0, "message": "No confirmed children to cascade to"}

    for cid in child_ids:
        await db.execute(
            text("""
                INSERT INTO audit_log (event_type, table_id, actor, details_json)
                VALUES ('cascade_review_queued', :cid, :actor, CAST(:d AS jsonb))
            """),
            {
                "cid": cid,
                "actor": user["email"],
                "d": f'{{"triggered_by":"{table_id}"}}',
            },
        )
    await db.commit()
    return {"queued": len(child_ids), "child_table_ids": child_ids}
