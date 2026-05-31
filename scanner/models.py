import uuid

from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class ScannerEvent(Base):
    __tablename__ = "scanner_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(String(255), unique=True, nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    source_name = Column(String(1000), nullable=False)
    data_source_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    raw_event = Column(JSONB, nullable=False)
    owner_email = Column(String(255), nullable=True)
    bucket = Column(String(255), nullable=True)
    file_format = Column(String(50), nullable=True)
    estimated_row_count = Column(BigInteger, nullable=True)
    column_count = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ColumnSample(Base):
    __tablename__ = "column_samples"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scanner_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scanner_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    table_id = Column(String(255), nullable=False, index=True)
    column_name = Column(String(255), nullable=False)
    column_dtype = Column(String(100), nullable=True)
    sample_count = Column(Integer, nullable=True)
    sample_s3_path = Column(String(1000), nullable=True)
    status = Column(String(50), nullable=False, default="sampled")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    """
    Append-only audit trail. DB-level trigger prevents UPDATE and DELETE.
    """

    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(100), nullable=False)
    table_id = Column(String(255), nullable=True, index=True)
    actor = Column(String(255), nullable=False, default="system")
    timestamp = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    details_json = Column(JSONB, nullable=True)


class ModelRegistry(Base):
    """Versioned model artifacts — one row per trained release."""

    __tablename__ = "model_registry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(100), nullable=False, unique=True)
    s3_uri = Column(String(1000), nullable=False)
    macro_f1 = Column(Float, nullable=True)
    weighted_f1 = Column(Float, nullable=True)
    accuracy = Column(Float, nullable=True)
    fixture_accuracy = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, default="candidate")
    trained_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    approved_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)


class PIIFinding(Base):
    """One row per column classification result from the inference service."""

    __tablename__ = "pii_findings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scanner_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("scanner_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    column_sample_id = Column(
        UUID(as_uuid=True),
        ForeignKey("column_samples.id", ondelete="CASCADE"),
        nullable=True,
    )
    table_id = Column(String(255), nullable=False, index=True)
    column_name = Column(String(255), nullable=False)
    pii_category = Column(String(50), nullable=False)
    confidence = Column(Float, nullable=False)
    flagged = Column(Integer, nullable=False, default=0)  # 0=False, 1=True (BOOLEAN in DB)
    # sampled → classified → flagged | clean → quarantined → remediated
    status = Column(String(50), nullable=False, default="classified")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class QuarantineManifest(Base):
    """One row per quarantine operation — tracks movement of data to the quarantine bucket."""

    __tablename__ = "quarantine_manifest"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    table_id = Column(String(255), nullable=False, index=True)
    source_s3_path = Column(String(1000), nullable=False)
    quarantine_s3_path = Column(String(1000), nullable=False)
    flagged_categories = Column(JSONB, nullable=True)
    file_count = Column(Integer, nullable=True)
    total_bytes = Column(BigInteger, nullable=True)
    status = Column(String(50), nullable=False, default="quarantined")
    quarantined_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(String(255), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)


class Notification(Base):
    """Delivery log for DPO email and Slack alerts."""

    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    notification_type = Column(String(50), nullable=False)  # email | slack
    recipient = Column(String(500), nullable=False)
    subject = Column(String(500), nullable=True)
    table_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="pending")  # pending | sent | failed
    sent_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
