import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DataSourceType(str, Enum):
    ATHENA = "athena"
    GLUE = "glue"
    S3_PARQUET = "s3_parquet"
    S3_CSV = "s3_csv"
    S3_JSON = "s3_json"
    UNKNOWN = "unknown"


class FileFormat(str, Enum):
    PARQUET = "parquet"
    CSV = "csv"
    JSON = "json"
    ORC = "orc"
    AVRO = "avro"
    UNKNOWN = "unknown"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TableCreatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    table_name: str
    database_name: str
    data_source_type: DataSourceType
    owner_email: Optional[str] = None
    column_count: Optional[int] = None
    location: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class FileMovedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    bucket: str
    prefix: str
    file_format: FileFormat = FileFormat.UNKNOWN
    estimated_row_count: Optional[int] = None
    file_size_bytes: Optional[int] = None
    owner_email: Optional[str] = None
    moved_at: datetime = Field(default_factory=_utcnow)


class PIICandidateEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_event_id: str
    source_name: str
    data_source_type: DataSourceType
    estimated_column_count: int = 0
    owner_email: Optional[str] = None
    bucket: Optional[str] = None
    file_format: Optional[FileFormat] = None
    estimated_row_count: Optional[int] = None
    enqueued_at: datetime = Field(default_factory=_utcnow)
