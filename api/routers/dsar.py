"""
DSAR (Data Subject Access Request) search endpoint.
Searches detected PII tables by identifier type.
The raw identifier value is never stored — only its SHA-256 hash.
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, require_role
from api.db import get_db

router = APIRouter(prefix="/api/v1/dsar", tags=["dsar"])

_CATEGORY_MAP: dict[str, str] = {
    "email":       "EMAIL",
    "national_id": "SSN",
    "phone":       "PHONE",
}


@router.post("/search")
async def dsar_search(
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("dpo", "admin")),
) -> dict:
    identifier_type = body.get("identifier_type", "email")
    identifier_value = str(body.get("identifier_value", ""))

    if identifier_type not in _CATEGORY_MAP:
        return {
            "error": "Invalid identifier_type. Accepted: email, national_id, phone"
        }

    pii_category = _CATEGORY_MAP[identifier_type]
    search_hash = hashlib.sha256(identifier_value.encode()).hexdigest()
    search_id = str(uuid.uuid4())

    rows = await db.execute(
        text("""
            SELECT
                se.source_name,
                pf.table_id,
                se.data_source_type,
                se.owner_email,
                se.estimated_row_count,
                max(pf.created_at) AS last_scanned,
                CASE
                    WHEN count(*) FILTER (WHERE pf.status = 'quarantined') > 0 THEN 'quarantined'
                    WHEN count(*) FILTER (WHERE pf.status = 'remediated')  > 0 THEN 'remediated'
                    WHEN count(*) FILTER (WHERE pf.status = 'flagged')     > 0 THEN 'flagged'
                    ELSE 'classified'
                END AS status,
                max(pf.confidence) AS confidence
            FROM pii_findings pf
            JOIN scanner_events se ON se.id = pf.scanner_event_id
            WHERE pf.pii_category = :cat AND pf.flagged = true
            GROUP BY se.source_name, pf.table_id, se.data_source_type,
                     se.owner_email, se.estimated_row_count
            ORDER BY max(pf.confidence) DESC
        """),
        {"cat": pii_category},
    )

    matches = [
        {
            "source_name": r.source_name,
            "table_id": r.table_id,
            "data_source_type": r.data_source_type,
            "owner_email": r.owner_email,
            "estimated_row_count": r.estimated_row_count,
            "last_scanned": r.last_scanned.isoformat() if r.last_scanned else None,
            "status": r.status,
            "confidence": round(float(r.confidence), 2),
        }
        for r in rows.fetchall()
    ]

    await db.execute(
        text("""
            INSERT INTO dsar_searches
                (search_id, initiated_by, identifier_type, search_hash,
                 tables_searched_count, tables_matched_count)
            VALUES (:sid, :actor, :itype, :hash, :searched, :matched)
        """),
        {
            "sid": search_id,
            "actor": user["email"],
            "itype": identifier_type,
            "hash": search_hash,
            "searched": len(matches),
            "matched": len(matches),
        },
    )
    await db.execute(
        text("""
            INSERT INTO audit_log (event_type, table_id, actor, details_json)
            VALUES ('dsar_search_initiated', :sid, :actor, CAST(:d AS jsonb))
        """),
        {
            "sid": search_id,
            "actor": user["email"],
            "d": (
                f'{{"search_id":"{search_id}",'
                f'"identifier_type":"{identifier_type}",'
                f'"tables_matched":{len(matches)}}}'
            ),
        },
    )
    await db.commit()

    return {
        "search_id": search_id,
        "identifier_type": identifier_type,
        "pii_category_searched": pii_category,
        "tables_matched": len(matches),
        "matches": matches,
        "note": (
            "Tables detected as containing this PII category. "
            "Row-level presence requires manual investigation per table."
        ),
    }


@router.get("/searches")
async def list_searches(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("dpo", "admin")),
) -> dict:
    rows = await db.execute(text("""
        SELECT search_id, initiated_by, identifier_type,
               tables_searched_count, tables_matched_count, created_at
        FROM dsar_searches
        ORDER BY created_at DESC
        LIMIT 50
    """))
    return {
        "searches": [
            {
                "search_id": str(r.search_id),
                "initiated_by": r.initiated_by,
                "identifier_type": r.identifier_type,
                "tables_searched_count": r.tables_searched_count,
                "tables_matched_count": r.tables_matched_count,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows.fetchall()
        ]
    }
