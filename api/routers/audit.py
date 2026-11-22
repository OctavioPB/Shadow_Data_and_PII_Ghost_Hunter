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
from api.services.export import csv_safe

router = APIRouter(prefix="/api/v1", tags=["audit"])


def _build_filters(
    actor: str | None,
    event_type: str | None,
    table_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> tuple[str, dict[str, Any]]:
    """Return a parameterized WHERE clause shared by the list and export endpoints."""
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
    return " AND ".join(conditions), params


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
    where, params = _build_filters(actor, event_type, table_id, date_from, date_to)

    rows = await db.execute(
        text(
            f"""
            SELECT id::text, event_type, table_id, actor, timestamp, details_json
            FROM audit_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT :limit OFFSET :offset
        """
        ),
        {**params, "limit": size, "offset": (page - 1) * size},
    )
    total = await db.execute(text(f"SELECT count(*) FROM audit_log WHERE {where}"), params)

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
    where, params = _build_filters(actor, event_type, table_id, date_from, date_to)
    rows = await db.execute(
        text(
            f"""
            SELECT id::text, event_type, table_id, actor, timestamp
            FROM audit_log
            WHERE {where}
            ORDER BY timestamp DESC
        """
        ),
        params,
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "event_type", "table_id", "actor", "timestamp"])
    for r in rows.fetchall():
        writer.writerow(
            [
                r.id,
                csv_safe(r.event_type),
                csv_safe(r.table_id or ""),
                csv_safe(r.actor),
                r.timestamp.isoformat(),
            ]
        )

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_log.csv"},
    )
