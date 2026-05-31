from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RiskItem(BaseModel):
    table_id: str
    source_name: str
    data_source_type: str
    pii_categories: list[str]
    max_confidence: float
    status: str
    flagged_column_count: int
    last_scanned: datetime
    owner_email: str | None = None


class RisksResponse(BaseModel):
    items: list[RiskItem]
    total: int
    page: int
    size: int
    pages: int


class StatsSummary(BaseModel):
    total_flagged: int
    remediated: int
    pending_review: int
    compliance_score: float


class ColumnFinding(BaseModel):
    column_name: str
    pii_category: str
    confidence: float
    sample_count: int | None = None
    status: str


class PIIReport(BaseModel):
    table_id: str
    source_name: str
    data_source_type: str
    owner_email: str | None = None
    flagged_columns: list[ColumnFinding]
    last_scanned: datetime | None = None


class RemediateRequest(BaseModel):
    action: str = Field(..., pattern="^(anonymize|quarantine|false_positive)$")
    notes: str | None = None


class RemediateResponse(BaseModel):
    table_id: str
    action: str
    status: str
    message: str


class DataSource(BaseModel):
    source_name: str
    data_source_type: str
    bucket: str | None = None
    region: str | None = None
    table_count: int
    flagged_count: int
    max_confidence: float
    pii_categories: list[str]


class DataSourcesResponse(BaseModel):
    items: list[DataSource]
    total: int
