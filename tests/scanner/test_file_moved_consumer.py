import json
from unittest.mock import MagicMock, patch

import pytest

from scanner.consumers.file_moved_consumer import _parse_s3_eventbridge
from scanner.schemas.events import DataSourceType, FileFormat, FileMovedEvent


# ─── _parse_s3_eventbridge ───────────────────────────────────────────────────


def test_parse_parquet_file():
    raw = {
        "id": "evt-s3-001",
        "source": "aws.s3",
        "detail": {
            "bucket": {"name": "my-datalake"},
            "object": {"key": "data/users/2024/01/data.parquet", "size": 2_048_000},
        },
    }
    event = _parse_s3_eventbridge(raw)
    assert event.bucket == "my-datalake"
    assert event.prefix == "data/users/2024/01/data.parquet"
    assert event.file_format == FileFormat.PARQUET
    assert event.file_size_bytes == 2_048_000
    assert event.event_id == "evt-s3-001"


def test_parse_csv_file():
    raw = {
        "id": "evt-s3-002",
        "detail": {
            "bucket": {"name": "landing-zone"},
            "object": {"key": "exports/orders.csv", "size": 512},
        },
    }
    event = _parse_s3_eventbridge(raw)
    assert event.file_format == FileFormat.CSV


def test_parse_json_file():
    raw = {
        "id": "evt-s3-003",
        "detail": {
            "bucket": {"name": "raw"},
            "object": {"key": "events/clicks.json", "size": 1024},
        },
    }
    event = _parse_s3_eventbridge(raw)
    assert event.file_format == FileFormat.JSON


def test_parse_unknown_extension_is_unknown():
    raw = {
        "id": "evt-s3-004",
        "detail": {
            "bucket": {"name": "bucket"},
            "object": {"key": "archive/dump.xml", "size": 100},
        },
    }
    event = _parse_s3_eventbridge(raw)
    assert event.file_format == FileFormat.UNKNOWN


def test_parse_missing_id_sets_empty_string():
    raw = {
        "detail": {
            "bucket": {"name": "b"},
            "object": {"key": "file.orc", "size": 0},
        }
    }
    event = _parse_s3_eventbridge(raw)
    assert event.event_id == ""
    assert event.file_format == FileFormat.ORC


def test_parse_bare_detail_dict():
    """Tolerates a payload that is already the detail dict (no EventBridge envelope)."""
    raw = {
        "bucket": {"name": "bare-bucket"},
        "object": {"key": "data.parquet", "size": 999},
    }
    event = _parse_s3_eventbridge(raw)
    assert event.bucket == "bare-bucket"
    assert event.file_format == FileFormat.PARQUET


def test_parse_missing_bucket_raises():
    raw = {"id": "x", "detail": {"object": {"key": "f.parquet", "size": 1}}}
    with pytest.raises(KeyError):
        _parse_s3_eventbridge(raw)


# ─── _handle ─────────────────────────────────────────────────────────────────


@pytest.fixture
def file_consumer():
    from scanner.config import ScannerSettings
    from scanner.consumers.file_moved_consumer import FileMovedConsumer

    settings = ScannerSettings()
    db = MagicMock()
    producer = MagicMock()
    with (
        patch("scanner.consumers.base_consumer.Consumer"),
        patch("scanner.consumers.base_consumer.Producer"),
    ):
        c = FileMovedConsumer(settings, db, producer)
    return c, db, producer


def test_handle_maps_parquet_to_s3_parquet_source(file_consumer):
    c, _, producer_mock = file_consumer
    with patch("scanner.consumers.file_moved_consumer.upsert_scanner_event"):
        event = FileMovedEvent(
            event_id="e1",
            bucket="my-bucket",
            prefix="data/file.parquet",
            file_format=FileFormat.PARQUET,
        )
        c._handle(event)
        candidate = producer_mock.publish.call_args.args[0]
        assert candidate.data_source_type == DataSourceType.S3_PARQUET
        assert candidate.bucket == "my-bucket"


def test_handle_maps_csv_to_s3_csv_source(file_consumer):
    c, _, producer_mock = file_consumer
    with patch("scanner.consumers.file_moved_consumer.upsert_scanner_event"):
        event = FileMovedEvent(
            event_id="e2",
            bucket="b",
            prefix="f.csv",
            file_format=FileFormat.CSV,
        )
        c._handle(event)
        candidate = producer_mock.publish.call_args.args[0]
        assert candidate.data_source_type == DataSourceType.S3_CSV


def test_handle_unknown_format_maps_to_unknown_source(file_consumer):
    c, _, producer_mock = file_consumer
    with patch("scanner.consumers.file_moved_consumer.upsert_scanner_event"):
        event = FileMovedEvent(
            event_id="e3",
            bucket="b",
            prefix="f.xml",
            file_format=FileFormat.UNKNOWN,
        )
        c._handle(event)
        candidate = producer_mock.publish.call_args.args[0]
        assert candidate.data_source_type == DataSourceType.UNKNOWN


def test_handle_generates_fallback_event_id_when_empty(file_consumer):
    c, _, _ = file_consumer
    with patch(
        "scanner.consumers.file_moved_consumer.upsert_scanner_event"
    ) as mock_upsert:
        event = FileMovedEvent(
            event_id="",
            bucket="my-bucket",
            prefix="data/file.parquet",
            file_format=FileFormat.PARQUET,
        )
        c._handle(event)
        kw = mock_upsert.call_args.kwargs
        assert kw["event_id"].startswith("file-my-bucket-")


def test_handle_source_name_is_s3_uri(file_consumer):
    c, _, _ = file_consumer
    with patch(
        "scanner.consumers.file_moved_consumer.upsert_scanner_event"
    ) as mock_upsert:
        event = FileMovedEvent(
            event_id="e5",
            bucket="analytics",
            prefix="prod/orders/part-00.parquet",
            file_format=FileFormat.PARQUET,
        )
        c._handle(event)
        kw = mock_upsert.call_args.kwargs
        assert kw["source_name"] == "s3://analytics/prod/orders/part-00.parquet"
