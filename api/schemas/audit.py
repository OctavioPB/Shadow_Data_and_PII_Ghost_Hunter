from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEntry(BaseModel):
    id: str
    event_type: str
    table_id: str | None
    actor: str
    timestamp: datetime
    details_json: dict[str, Any] | None = None


class AuditLogResponse(BaseModel):
    items: list[AuditEntry]
    total: int
    page: int
    size: int
