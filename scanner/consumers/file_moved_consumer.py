import json
import logging
from pathlib import PurePosixPath

from pydantic import BaseModel
from sqlalchemy.orm import Session

from scanner.config import ScannerSettings
from scanner.consumers.base_consumer import BaseConsumer
from scanner.producers.pii_candidate_producer import PIICandidateProducer
from scanner.repository import upsert_scanner_event
from scanner.schemas.events import DataSourceType, FileFormat, FileMovedEvent, PIICandidateEvent

logger = logging.getLogger(__name__)

_EXT_TO_FORMAT: dict[str, FileFormat] = {
    ".parquet": FileFormat.PARQUET,
    ".csv": FileFormat.CSV,
    ".json": FileFormat.JSON,
    ".orc": FileFormat.ORC,
    ".avro": FileFormat.AVRO,
}

_FORMAT_TO_SOURCE: dict[FileFormat, DataSourceType] = {
    FileFormat.PARQUET: DataSourceType.S3_PARQUET,
    FileFormat.CSV: DataSourceType.S3_CSV,
    FileFormat.JSON: DataSourceType.S3_JSON,
}


def _parse_s3_eventbridge(raw: dict) -> FileMovedEvent:
    """
    Parse an AWS EventBridge 'Object Created' notification into FileMovedEvent.

    Tolerates both the full EventBridge envelope and a bare detail dict.
    """
    detail = raw.get("detail", raw)
    bucket: str = detail["bucket"]["name"]
    obj: dict = detail["object"]
    key: str = obj["key"]
    size: int = obj.get("size", 0)

    ext = PurePosixPath(key).suffix.lower()
    file_format = _EXT_TO_FORMAT.get(ext, FileFormat.UNKNOWN)

    return FileMovedEvent(
        event_id=raw.get("id", ""),
        bucket=bucket,
        prefix=key,
        file_format=file_format,
        file_size_bytes=size if size > 0 else None,
    )


class FileMovedConsumer(BaseConsumer):
    """
    Reads `file.moved` events (AWS S3 EventBridge format), persists them,
    and publishes an enriched PIICandidate downstream.
    """

    topic = "file.moved"

    def __init__(
        self,
        settings: ScannerSettings,
        db: Session,
        producer: PIICandidateProducer,
    ) -> None:
        super().__init__(settings)
        self._db = db
        self._producer = producer

    def _parse(self, raw: bytes) -> FileMovedEvent:
        data = json.loads(raw.decode("utf-8"))
        return _parse_s3_eventbridge(data)

    def _handle(self, event: BaseModel) -> None:
        assert isinstance(event, FileMovedEvent)

        data_source_type = _FORMAT_TO_SOURCE.get(event.file_format, DataSourceType.UNKNOWN)

        # Fall back to a deterministic key when EventBridge omits the id field
        event_id = event.event_id or f"file-{event.bucket}-{event.prefix}"

        upsert_scanner_event(
            self._db,
            event_id=event_id,
            event_type="file.moved",
            source_name=f"s3://{event.bucket}/{event.prefix}",
            data_source_type=data_source_type.value,
            bucket=event.bucket,
            file_format=event.file_format.value,
            estimated_row_count=event.estimated_row_count,
            raw_event=event.model_dump(mode="json"),
        )

        candidate = PIICandidateEvent(
            source_event_id=event_id,
            source_name=f"s3://{event.bucket}/{event.prefix}",
            data_source_type=data_source_type,
            bucket=event.bucket,
            file_format=event.file_format,
            estimated_row_count=event.estimated_row_count,
        )
        self._producer.publish(candidate)

        logger.info(
            "file.moved → pii.candidates | bucket=%s key=%s format=%s",
            event.bucket,
            event.prefix,
            event.file_format.value,
        )
