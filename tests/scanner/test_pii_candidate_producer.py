import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scanner.schemas.events import DataSourceType, FileFormat, PIICandidateEvent


# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_producer(settings=None):
    from scanner.config import ScannerSettings
    from scanner.producers.pii_candidate_producer import PIICandidateProducer

    s = settings or ScannerSettings()
    with (
        patch("scanner.producers.pii_candidate_producer.SchemaRegistryClient"),
        patch("scanner.producers.pii_candidate_producer.AvroSerializer") as mock_ser_cls,
        patch("scanner.producers.pii_candidate_producer.Producer") as mock_kafka_cls,
    ):
        mock_ser_cls.return_value = MagicMock(side_effect=lambda ev, ctx: b"avro-bytes")
        p = PIICandidateProducer(s)
        p._kafka_cls_mock = mock_kafka_cls
    return p


# ─── Schema file ─────────────────────────────────────────────────────────────


def test_avro_schema_file_exists():
    schema_path = (
        Path(__file__).parent.parent.parent
        / "scanner"
        / "schemas"
        / "avro"
        / "pii_candidate.avsc"
    )
    assert schema_path.exists(), "Avro schema file is missing"


def test_avro_schema_is_valid_json():
    schema_path = (
        Path(__file__).parent.parent.parent
        / "scanner"
        / "schemas"
        / "avro"
        / "pii_candidate.avsc"
    )
    schema = json.loads(schema_path.read_text())
    assert schema["type"] == "record"
    assert schema["name"] == "PIICandidate"
    field_names = [f["name"] for f in schema["fields"]]
    assert "event_id" in field_names
    assert "data_source_type" in field_names
    assert "enqueued_at" in field_names


# ─── publish ─────────────────────────────────────────────────────────────────


def test_publish_calls_produce_with_correct_topic():
    p = make_producer()
    event = PIICandidateEvent(
        source_event_id="src-001",
        source_name="db.users_copy",
        data_source_type=DataSourceType.GLUE,
        estimated_column_count=8,
    )
    p.publish(event)
    p._producer.produce.assert_called_once()
    kw = p._producer.produce.call_args.kwargs
    assert kw["topic"] == "pii.candidates"


def test_publish_uses_source_event_id_as_key():
    p = make_producer()
    event = PIICandidateEvent(
        source_event_id="src-XYZ",
        source_name="s3://b/f.parquet",
        data_source_type=DataSourceType.S3_PARQUET,
    )
    p.publish(event)
    kw = p._producer.produce.call_args.kwargs
    assert kw["key"] == b"src-XYZ"


def test_publish_triggers_poll_for_delivery_callbacks():
    p = make_producer()
    event = PIICandidateEvent(
        source_event_id="s",
        source_name="n",
        data_source_type=DataSourceType.UNKNOWN,
    )
    p.publish(event)
    p._producer.poll.assert_called_once_with(0)


# ─── Idempotent producer configuration ───────────────────────────────────────


def test_producer_config_is_idempotent():
    from scanner.config import ScannerSettings
    from scanner.producers.pii_candidate_producer import PIICandidateProducer

    with (
        patch("scanner.producers.pii_candidate_producer.SchemaRegistryClient"),
        patch("scanner.producers.pii_candidate_producer.AvroSerializer"),
        patch("scanner.producers.pii_candidate_producer.Producer") as mock_cls,
    ):
        PIICandidateProducer(ScannerSettings())
        config: dict = mock_cls.call_args.args[0]
        assert config["enable.idempotence"] is True
        assert config["acks"] == "all"
        assert config["retries"] == 3
        assert config["max.in.flight.requests.per.connection"] == 5


# ─── _event_to_dict ──────────────────────────────────────────────────────────


def test_event_to_dict_serializes_enums_as_strings():
    from scanner.producers.pii_candidate_producer import _event_to_dict

    event = PIICandidateEvent(
        source_event_id="s",
        source_name="n",
        data_source_type=DataSourceType.S3_CSV,
        file_format=FileFormat.CSV,
    )
    result = _event_to_dict(event, MagicMock())
    assert result["data_source_type"] == "s3_csv"
    assert result["file_format"] == "csv"
    assert isinstance(result["enqueued_at"], str)


def test_event_to_dict_none_file_format_stays_none():
    from scanner.producers.pii_candidate_producer import _event_to_dict

    event = PIICandidateEvent(
        source_event_id="s",
        source_name="n",
        data_source_type=DataSourceType.ATHENA,
    )
    result = _event_to_dict(event, MagicMock())
    assert result["file_format"] is None
