import json
import logging

from pydantic import BaseModel
from sqlalchemy.orm import Session

from scanner.config import ScannerSettings
from scanner.consumers.base_consumer import BaseConsumer
from scanner.producers.pii_candidate_producer import PIICandidateProducer
from scanner.repository import upsert_scanner_event
from scanner.schemas.events import PIICandidateEvent, TableCreatedEvent

logger = logging.getLogger(__name__)


class TableCreatedConsumer(BaseConsumer):
    """
    Reads `table.created` events, persists them to scanner_events,
    and publishes an enriched PIICandidate to the downstream topic.
    """

    topic = "table.created"

    def __init__(
        self,
        settings: ScannerSettings,
        db: Session,
        producer: PIICandidateProducer,
    ) -> None:
        super().__init__(settings)
        self._db = db
        self._producer = producer

    def _parse(self, raw: bytes) -> TableCreatedEvent:
        data = json.loads(raw.decode("utf-8"))
        return TableCreatedEvent.model_validate(data)

    def _handle(self, event: BaseModel) -> None:
        assert isinstance(event, TableCreatedEvent)

        upsert_scanner_event(
            self._db,
            event_id=event.event_id,
            event_type="table.created",
            source_name=f"{event.database_name}.{event.table_name}",
            data_source_type=event.data_source_type.value,
            owner_email=event.owner_email,
            column_count=event.column_count,
            raw_event=event.model_dump(mode="json"),
        )

        candidate = PIICandidateEvent(
            source_event_id=event.event_id,
            source_name=f"{event.database_name}.{event.table_name}",
            data_source_type=event.data_source_type,
            estimated_column_count=event.column_count or 0,
            owner_email=event.owner_email,
        )
        self._producer.publish(candidate)

        logger.info(
            "table.created → pii.candidates | table=%s.%s source=%s",
            event.database_name,
            event.table_name,
            event.data_source_type.value,
        )
