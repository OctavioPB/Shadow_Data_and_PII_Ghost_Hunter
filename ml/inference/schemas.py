"""
Pydantic request/response schemas for the PII inference service.

Privacy guarantee: this module never mentions raw PII values in its
log-friendly string representations — only column_id, pii_category,
and confidence scores are exposed in the response.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ColumnInput(BaseModel):
    column_id: str
    column_name: str
    # Up to 1,000 sampled cell values; intentionally not logged
    values: list[str] = Field(default_factory=list, max_length=1_000)


class InferRequest(BaseModel):
    table_id: str
    columns: list[ColumnInput] = Field(min_length=1, max_length=50)


class ColumnResult(BaseModel):
    column_id: str
    pii_category: str
    confidence: float
    flagged: bool


class InferResponse(BaseModel):
    table_id: str
    results: list[ColumnResult]


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    version: str | None = None
