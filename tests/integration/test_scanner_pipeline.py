"""
Integration tests for the scanner pipeline.

Spins up real Kafka and PostgreSQL containers via Testcontainers to validate
the end-to-end flow:
  table.created event → DB persist → pii.candidates publish

Requires Docker. Skipped automatically when the `integration` mark is not
selected (run with: pytest -m integration tests/integration/)
"""

import json
import threading
import time
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from scanner.config import ScannerSettings
from scanner.models import Base, ScannerEvent
from scanner.schemas.events import DataSourceType, TableCreatedEvent

pytestmark = pytest.mark.integration


# ─── Containers ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def kafka_container() -> Generator[KafkaContainer, None, None]:
    with KafkaContainer("confluentinc/cp-kafka:7.5.0") as kafka:
        yield kafka


@pytest.fixture(scope="module")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    with PostgresContainer("postgres:15", dbname="pii_hunter") as pg:
        yield pg


@pytest.fixture(scope="module")
def db_engine(postgres_container: PostgresContainer):
    url = postgres_container.get_connection_url()
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="module")
def kafka_bootstrap(kafka_container: KafkaContainer) -> str:
    return kafka_container.get_bootstrap_server()


@pytest.fixture(scope="module")
def kafka_topics(kafka_bootstrap: str) -> None:
    """Create the topics the scanner expects."""
    admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
    topics = [
        NewTopic("table.created", num_partitions=1, replication_factor=1),
        NewTopic("pii.candidates", num_partitions=1, replication_factor=1),
        NewTopic("table.created.dlq", num_partitions=1, replication_factor=1),
    ]
    futures = admin.create_topics(topics)
    for topic, f in futures.items():
        try:
            f.result()
        except Exception:
            pass  # topic may already exist


# ─── Helpers ─────────────────────────────────────────────────────────────────


def publish_event(bootstrap: str, topic: str, payload: dict) -> None:
    p = Producer({"bootstrap.servers": bootstrap})
    p.produce(topic, json.dumps(payload).encode())
    p.flush(timeout=10)


def consume_one(bootstrap: str, topic: str, timeout: float = 15.0) -> dict | None:
    c = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": "test-consumer",
            "auto.offset.reset": "earliest",
        }
    )
    c.subscribe([topic])
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            msg = c.poll(1.0)
            if msg and not msg.error():
                return json.loads(msg.value().decode())
        return None
    finally:
        c.close()


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_table_created_event_persisted_to_db(
    kafka_bootstrap: str,
    kafka_topics: None,
    db_engine,
):
    """A table.created event must appear in scanner_events after the consumer runs."""
    settings = ScannerSettings(
        kafka_bootstrap_servers=kafka_bootstrap,
        kafka_security_protocol="PLAINTEXT",
        kafka_schema_registry_url="http://localhost:8081",  # not used in this test
        database_url=db_engine.url.render_as_string(hide_password=False),
    )

    event = TableCreatedEvent(
        event_id="integ-001",
        table_name="customer_export",
        database_name="prod_backup",
        data_source_type=DataSourceType.GLUE,
        column_count=7,
    )
    publish_event(kafka_bootstrap, "table.created", event.model_dump(mode="json"))

    # Run the consumer in a thread; stop it after one successful commit
    from scanner.consumers.table_created_consumer import TableCreatedConsumer

    db_session = Session(db_engine)
    producer_mock = MagicMock()

    with (
        patch("scanner.producers.pii_candidate_producer.SchemaRegistryClient"),
        patch("scanner.producers.pii_candidate_producer.AvroSerializer"),
        patch("scanner.producers.pii_candidate_producer.Producer"),
    ):
        consumer = TableCreatedConsumer(settings, db_session, producer_mock)

    processed = threading.Event()
    original_handle = consumer._handle

    def handle_and_stop(ev):
        original_handle(ev)
        consumer.stop()
        processed.set()

    consumer._handle = handle_and_stop  # type: ignore[method-assign]

    thread = threading.Thread(target=consumer.run, daemon=True)
    thread.start()
    assert processed.wait(timeout=20), "Consumer did not process the event within 20 s"
    thread.join(timeout=5)

    with Session(db_engine) as s:
        row = s.execute(
            select(ScannerEvent).where(ScannerEvent.event_id == "integ-001")
        ).scalar_one_or_none()

    assert row is not None, "scanner_events row not found"
    assert row.event_type == "table.created"
    assert row.source_name == "prod_backup.customer_export"
    assert row.status == "pending"


def test_table_created_event_published_to_pii_candidates(
    kafka_bootstrap: str,
    kafka_topics: None,
    db_engine,
):
    """After processing a table.created event, a PIICandidate must appear on pii.candidates."""
    settings = ScannerSettings(
        kafka_bootstrap_servers=kafka_bootstrap,
        kafka_security_protocol="PLAINTEXT",
        kafka_schema_registry_url="http://localhost:8081",
        database_url=db_engine.url.render_as_string(hide_password=False),
    )

    event = TableCreatedEvent(
        event_id="integ-002",
        table_name="invoices_bkp",
        database_name="finance",
        data_source_type=DataSourceType.ATHENA,
        column_count=15,
    )
    publish_event(kafka_bootstrap, "table.created", event.model_dump(mode="json"))

    # Use a real producer that writes JSON (Avro serializer is bypassed)
    kafka_producer = Producer({"bootstrap.servers": kafka_bootstrap})

    from scanner.consumers.table_created_consumer import TableCreatedConsumer
    from scanner.producers.pii_candidate_producer import PIICandidateProducer

    with (
        patch("scanner.producers.pii_candidate_producer.SchemaRegistryClient"),
        patch("scanner.producers.pii_candidate_producer.AvroSerializer") as mock_ser,
        patch("scanner.producers.pii_candidate_producer.Producer") as mock_prod_cls,
    ):
        # Serialize as plain JSON for the integration test
        def json_serialize(ev, ctx):
            return ev.model_dump_json().encode()

        mock_ser.return_value = MagicMock(side_effect=json_serialize)
        mock_prod_cls.return_value = kafka_producer
        pii_producer = PIICandidateProducer(settings)

    db_session = Session(db_engine)
    consumer = TableCreatedConsumer(settings, db_session, pii_producer)

    processed = threading.Event()
    original_handle = consumer._handle

    def handle_and_stop(ev):
        original_handle(ev)
        consumer.stop()
        processed.set()

    consumer._handle = handle_and_stop  # type: ignore[method-assign]

    thread = threading.Thread(target=consumer.run, daemon=True)
    thread.start()
    assert processed.wait(timeout=20)
    kafka_producer.flush(timeout=5)
    thread.join(timeout=5)

    candidate_msg = consume_one(kafka_bootstrap, "pii.candidates", timeout=10)
    assert candidate_msg is not None, "No message on pii.candidates"
    assert candidate_msg["source_event_id"] == "integ-002"
    assert candidate_msg["data_source_type"] == "athena"


def test_malformed_event_routed_to_dlq(
    kafka_bootstrap: str,
    kafka_topics: None,
    db_engine,
):
    """A malformed message must land on table.created.dlq and not crash the consumer."""
    settings = ScannerSettings(
        kafka_bootstrap_servers=kafka_bootstrap,
        kafka_security_protocol="PLAINTEXT",
        kafka_schema_registry_url="http://localhost:8081",
        database_url=db_engine.url.render_as_string(hide_password=False),
    )

    publish_event(
        kafka_bootstrap,
        "table.created",
        {"garbage": True},  # missing all required fields
    )

    from scanner.consumers.table_created_consumer import TableCreatedConsumer

    db_session = Session(db_engine)
    producer_mock = MagicMock()
    consumer = TableCreatedConsumer(settings, db_session, producer_mock)

    dlq_received = threading.Event()
    original_dlq = consumer._send_to_dlq

    def capture_dlq(raw, error):
        original_dlq(raw, error)
        consumer.stop()
        dlq_received.set()

    consumer._send_to_dlq = capture_dlq  # type: ignore[method-assign]

    thread = threading.Thread(target=consumer.run, daemon=True)
    thread.start()
    assert dlq_received.wait(timeout=20), "Malformed event was not routed to DLQ"
    thread.join(timeout=5)

    dlq_msg = consume_one(kafka_bootstrap, "table.created.dlq", timeout=10)
    assert dlq_msg is not None
    assert "error" in dlq_msg
