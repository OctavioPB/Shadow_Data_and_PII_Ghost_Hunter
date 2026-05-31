from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db import get_db
from api.schemas.audit import AuditEntry, AuditLogResponse

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit-log", response_model=AuditLogResponse)
async def audit_log(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    actor: str | None = Query(None),
    event_type: str | None = Query(None),
    table_id: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> AuditLogResponse:
    conditions = ["1=1"]
    params: dict[str, Any] = {"limit": size, "offset": (page - 1) * size}

    if actor:
        conditions.append("actor ILIKE :actor")
        params["actor"] = f"%{actor}%"
    if event_type:
        conditions.append("event_type = :event_type")
        params["event_type"] = event_type
    if table_id:
        conditions.append("table_id = :table_id")
        params["table_id"] = table_id
    if date_from:
        conditions.append("timestamp >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("timestamp <= :date_to")
        params["date_to"] = date_to

    where = " AND ".join(conditions)

    rows = await db.execute(
        text(f"""
            SELECT id::text, event_type, table_id, actor, timestamp, details_json
            FROM audit_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    total = await db.execute(
        text(f"SELECT count(*) FROM audit_log WHERE {where}"),
        {k: v for k, v in params.items() if k not in ("limit", "offset")},
    )

    items = [
        AuditEntry(
            id=r.id,
            event_type=r.event_type,
            table_id=r.table_id,
            actor=r.actor,
            timestamp=r.timestamp,
            details_json=r.details_json,
        )
        for r in rows.fetchall()
    ]

    return AuditLogResponse(
        items=items,
        total=int(total.scalar() or 0),
        page=page,
        size=size,
    )


@router.get("/audit-log/export")
async def export_audit_log(
    actor: str | None = Query(None),
    event_type: str | None = Query(None),
    table_id: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
) -> StreamingResponse:
    conditions = ["1=1"]
    params: dict[str, Any] = {}

    if actor:
        conditions.append("actor ILIKE :actor")
        params["actor"] = f"%{actor}%"
    if event_type:
        conditions.append("event_type = :event_type")
        params["event_type"] = event_type
    if table_id:
        conditions.append("table_id = :table_id")
        params["table_id"] = table_id
    if date_from:
        conditions.append("timestamp >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("timestamp <= :date_to")
        params["date_to"] = date_to

    where = " AND ".join(conditions)
    rows = await db.execute(
        text(f"""
            SELECT id::text, event_type, table_id, actor, timestamp
            FROM audit_log
            WHERE {where}
            ORDER BY timestamp DESC
        """),
        params,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "event_type", "table_id", "actor", "timestamp"])
    for r in rows.fetchall():
        writer.writerow([r.id, r.event_type, r.table_id or "", r.actor, r.timestamp.isoformat()])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )
