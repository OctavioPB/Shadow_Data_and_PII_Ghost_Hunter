from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from scanner.models import ScannerEvent


def upsert_scanner_event(
    db: Session,
    *,
    event_id: str,
    event_type: str,
    source_name: str,
    data_source_type: str,
    raw_event: dict[str, Any],
    owner_email: Optional[str] = None,
    bucket: Optional[str] = None,
    file_format: Optional[str] = None,
    estimated_row_count: Optional[int] = None,
    column_count: Optional[int] = None,
) -> ScannerEvent:
    """Insert a scanner event; silently skips if event_id already exists."""
    values = {
        "event_id": event_id,
        "event_type": event_type,
        "source_name": source_name,
        "data_source_type": data_source_type,
        "raw_event": raw_event,
        "owner_email": owner_email,
        "bucket": bucket,
        "file_format": file_format,
        "estimated_row_count": estimated_row_count,
        "column_count": column_count,
    }
    stmt = insert(ScannerEvent).values(**values).on_conflict_do_nothing(
        index_elements=["event_id"]
    )
    db.execute(stmt)
    db.commit()
    return db.execute(
        select(ScannerEvent).where(ScannerEvent.event_id == event_id)
    ).scalar_one()


def get_pending_events(db: Session, limit: int = 100) -> list[ScannerEvent]:
    return list(
        db.execute(
            select(ScannerEvent)
            .where(ScannerEvent.status == "pending")
            .order_by(ScannerEvent.created_at)
            .limit(limit)
        ).scalars()
    )


def update_event_status(db: Session, event_id: str, status: str) -> None:
    event = db.execute(
        select(ScannerEvent).where(ScannerEvent.event_id == event_id)
    ).scalar_one_or_none()
    if event:
        event.status = status
        db.commit()
