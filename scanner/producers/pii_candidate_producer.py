import json
import logging
from pathlib import Path

from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer
from confluent_kafka.serialization import MessageField, SerializationContext

from scanner.config import ScannerSettings
from scanner.schemas.events import PIICandidateEvent

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "avro" / "pii_candidate.avsc"


def _event_to_dict(event: PIICandidateEvent, _ctx: SerializationContext) -> dict:
    d = event.model_dump(mode="json")
    # Avro enum fields must be plain strings, not dicts
    d["data_source_type"] = event.data_source_type.value
    if event.file_format is not None:
        d["file_format"] = event.file_format.value
    d["enqueued_at"] = event.enqueued_at.isoformat()
    return d


class PIICandidateProducer:
    """
    Publishes PIICandidateEvent to the pii.candidates topic using Avro serialization
    with the Confluent Schema Registry.

    Producer is configured for idempotent delivery (enable.idempotence=true, acks=all).
    """

    def __init__(self, settings: ScannerSettings) -> None:
        self._topic = settings.topic_pii_candidates
        self._settings = settings

        schema_str = _SCHEMA_PATH.read_text()

        schema_registry_client = SchemaRegistryClient(
            {"url": settings.kafka_schema_registry_url}
        )
        self._serializer = AvroSerializer(
            schema_registry_client,
            schema_str,
            _event_to_dict,
        )

        self._producer = Producer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "security.protocol": settings.kafka_security_protocol,
                # Idempotent delivery guarantees
                "enable.idempotence": True,
                "acks": "all",
                "retries": 3,
                "max.in.flight.requests.per.connection": 5,
                "delivery.timeout.ms": 30_000,
            }
        )

    def publish(self, event: PIICandidateEvent) -> None:
        ctx = SerializationContext(self._topic, MessageField.VALUE)
        value = self._serializer(event, ctx)
        self._producer.produce(
            topic=self._topic,
            key=event.source_event_id.encode("utf-8"),
            value=value,
            on_delivery=self._on_delivery,
        )
        # Trigger delivery callbacks without blocking
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0) -> None:
        self._producer.flush(timeout=timeout)

    @staticmethod
    def _on_delivery(err: Exception | None, msg: object) -> None:
        if err:
            logger.error("Delivery failed for pii.candidates: %s", err)
        else:
            logger.debug(
                "Delivered to %s [partition %s]", msg.topic(), msg.partition()  # type: ignore[union-attr]
            )
