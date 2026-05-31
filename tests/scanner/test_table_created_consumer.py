import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from scanner.schemas.events import DataSourceType, PIICandidateEvent, TableCreatedEvent


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def settings():
    from scanner.config import ScannerSettings

    return ScannerSettings(
        kafka_bootstrap_servers="localhost:9092",
        kafka_security_protocol="PLAINTEXT",
    )


@pytest.fixture
def consumer(settings):
    from scanner.consumers.table_created_consumer import TableCreatedConsumer

    db = MagicMock()
    producer = MagicMock()
    with (
        patch("scanner.consumers.base_consumer.Consumer"),
        patch("scanner.consumers.base_consumer.Producer"),
    ):
        c = TableCreatedConsumer(settings, db, producer)
    return c, db, producer


# ─── _parse ──────────────────────────────────────────────────────────────────


def test_parse_valid_event(consumer):
    c, _, _ = consumer
    payload = {
        "event_id": "abc-123",
        "table_name": "users_backup",
        "database_name": "prod_copy",
        "data_source_type": "glue",
    }
    event = c._parse(json.dumps(payload).encode())
    assert isinstance(event, TableCreatedEvent)
    assert event.table_name == "users_backup"
    assert event.data_source_type == DataSourceType.GLUE


def test_parse_missing_required_fields_raises(consumer):
    c, _, _ = consumer
    # Missing database_name and data_source_type
    payload = {"table_name": "only_table"}
    with pytest.raises((ValidationError, KeyError, ValueError)):
        c._parse(json.dumps(payload).encode())


def test_parse_invalid_json_raises(consumer):
    c, _, _ = consumer
    with pytest.raises((json.JSONDecodeError, ValueError)):
        c._parse(b"not-valid-json{{{")


def test_parse_invalid_enum_value_raises(consumer):
    c, _, _ = consumer
    payload = {
        "event_id": "x",
        "table_name": "t",
        "database_name": "d",
        "data_source_type": "oracle",  # not a valid DataSourceType
    }
    with pytest.raises(ValidationError):
        c._parse(json.dumps(payload).encode())


# ─── _handle ─────────────────────────────────────────────────────────────────


def test_handle_persists_event_to_db(consumer):
    c, db_mock, producer_mock = consumer
    with patch("scanner.consumers.table_created_consumer.upsert_scanner_event") as mock_upsert:
        event = TableCreatedEvent(
            event_id="evt-001",
            table_name="orders_backup",
            database_name="dw_staging",
            data_source_type=DataSourceType.ATHENA,
            column_count=5,
        )
        c._handle(event)

        mock_upsert.assert_called_once()
        kw = mock_upsert.call_args.kwargs
        assert kw["event_id"] == "evt-001"
        assert kw["event_type"] == "table.created"
        assert kw["source_name"] == "dw_staging.orders_backup"
        assert kw["data_source_type"] == "athena"
        assert kw["column_count"] == 5


def test_handle_publishes_pii_candidate(consumer):
    c, _, producer_mock = consumer
    with patch("scanner.consumers.table_created_consumer.upsert_scanner_event"):
        event = TableCreatedEvent(
            event_id="evt-002",
            table_name="payments",
            database_name="prod",
            data_source_type=DataSourceType.GLUE,
            column_count=12,
            owner_email="team@example.com",
        )
        c._handle(event)

        producer_mock.publish.assert_called_once()
        candidate: PIICandidateEvent = producer_mock.publish.call_args.args[0]
        assert candidate.source_event_id == "evt-002"
        assert candidate.estimated_column_count == 12
        assert candidate.owner_email == "team@example.com"
        assert candidate.data_source_type == DataSourceType.GLUE


def test_handle_without_column_count_defaults_to_zero(consumer):
    c, _, producer_mock = consumer
    with patch("scanner.consumers.table_created_consumer.upsert_scanner_event"):
        event = TableCreatedEvent(
            event_id="evt-003",
            table_name="tmp",
            database_name="dev",
            data_source_type=DataSourceType.GLUE,
        )
        c._handle(event)
        candidate: PIICandidateEvent = producer_mock.publish.call_args.args[0]
        assert candidate.estimated_column_count == 0


# ─── DLQ routing via base consumer ───────────────────────────────────────────


def test_malformed_event_is_routed_to_dlq(consumer):
    c, _, _ = consumer
    dlq_producer_mock = MagicMock()
    c._dlq_producer = dlq_producer_mock

    c._send_to_dlq(b'{"bad": true}', "ValidationError: field required")

    dlq_producer_mock.produce.assert_called_once()
    dlq_topic = dlq_producer_mock.produce.call_args.args[0]
    assert dlq_topic == "table.created.dlq"

    payload = json.loads(dlq_producer_mock.produce.call_args.args[1].decode())
    assert "ValidationError" in payload["error"]
