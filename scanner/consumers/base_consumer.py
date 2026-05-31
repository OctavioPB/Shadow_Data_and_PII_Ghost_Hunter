import json
import logging
import signal
import threading
import time
from abc import ABC, abstractmethod

from confluent_kafka import Consumer, KafkaError, KafkaException, Producer
from pydantic import BaseModel, ValidationError

from scanner.config import ScannerSettings
from scanner.metrics import event_processing_seconds, events_consumed_total, events_dlq_total

logger = logging.getLogger(__name__)


class BaseConsumer(ABC):
    """
    Poll-loop Kafka consumer with manual commit, DLQ routing, and Prometheus metrics.

    Subclasses must declare a class-level `topic: str` and implement
    `_parse()` and `_handle()`.
    """

    topic: str

    def __init__(self, settings: ScannerSettings) -> None:
        self._settings = settings
        self._running = False
        self._consumer = Consumer(self._consumer_config())
        self._dlq_producer = Producer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "security.protocol": settings.kafka_security_protocol,
            }
        )
        self._dlq_topic = f"{self.topic}{settings.topic_dlq_suffix}"

    def _consumer_config(self) -> dict:
        return {
            "bootstrap.servers": self._settings.kafka_bootstrap_servers,
            "security.protocol": self._settings.kafka_security_protocol,
            "group.id": self._settings.kafka_consumer_group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": "false",
        }

    @abstractmethod
    def _parse(self, raw: bytes) -> BaseModel:
        """Parse raw bytes into a validated Pydantic event model."""

    @abstractmethod
    def _handle(self, event: BaseModel) -> None:
        """Persist and publish a validated event downstream."""

    def run(self) -> None:
        """Start the blocking poll loop. Registers SIGINT/SIGTERM when called from main thread."""
        self._running = True

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._shutdown_handler)
            signal.signal(signal.SIGTERM, self._shutdown_handler)

        self._consumer.subscribe([self.topic])
        logger.info("Consumer started on topic %s", self.topic)

        try:
            while self._running:
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(msg.error())

                start = time.monotonic()
                try:
                    event = self._parse(msg.value())
                    self._handle(event)
                    self._consumer.commit(message=msg)
                    events_consumed_total.labels(
                        topic=self.topic,
                        consumer_group=self._settings.kafka_consumer_group,
                    ).inc()
                except (ValidationError, ValueError, KeyError) as exc:
                    logger.warning(
                        "Malformed event on %s — routing to DLQ: %s",
                        self.topic,
                        exc,
                    )
                    self._send_to_dlq(msg.value(), str(exc))
                    self._consumer.commit(message=msg)
                    events_dlq_total.labels(topic=self.topic).inc()
                finally:
                    event_processing_seconds.labels(topic=self.topic).observe(
                        time.monotonic() - start
                    )
        finally:
            self._consumer.close()
            logger.info("Consumer closed for topic %s", self.topic)

    def stop(self) -> None:
        self._running = False

    def _send_to_dlq(self, raw: bytes, error: str) -> None:
        payload = json.dumps(
            {"error": error, "original": raw.decode("utf-8", errors="replace")}
        ).encode()
        self._dlq_producer.produce(self._dlq_topic, payload)
        self._dlq_producer.flush(timeout=5.0)

    def _shutdown_handler(self, _signum: int, _frame: object) -> None:
        logger.info("Shutdown signal received — stopping consumer")
        self._running = False
