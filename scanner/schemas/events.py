import uuid
from datetime import UTC, datetime
from enum import Enum

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
    return datetime.now(UTC)


class TableCreatedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    table_name: str
    database_name: str
    data_source_type: DataSourceType
    owner_email: str | None = None
    column_count: int | None = None
    location: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class FileMovedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    bucket: str
    prefix: str
    file_format: FileFormat = FileFormat.UNKNOWN
    estimated_row_count: int | None = None
    file_size_bytes: int | None = None
    owner_email: str | None = None
    moved_at: datetime = Field(default_factory=_utcnow)


class PIICandidateEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_event_id: str
    source_name: str
    data_source_type: DataSourceType
    estimated_column_count: int = 0
    owner_email: str | None = None
    bucket: str | None = None
    file_format: FileFormat | None = None
    estimated_row_count: int | None = None
    enqueued_at: datetime = Field(default_factory=_utcnow)
