"""scanner.repository: idempotent event persistence and status updates."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from scanner.repository import get_pending_events, update_event_status, upsert_scanner_event


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_upsert_is_idempotent_on_event_id_and_returns_the_stored_row():
    db = MagicMock()
    stored = SimpleNamespace(event_id="evt-1")
    db.execute.return_value.scalar_one.return_value = stored

    result = upsert_scanner_event(
        db,
        event_id="evt-1",
        event_type="file.moved",
        source_name="s3://b/k.csv",
        data_source_type="s3_csv",
        raw_event={"id": "evt-1"},
        bucket="b",
    )

    insert_stmt = db.execute.call_args_list[0].args[0]
    sql = _sql(insert_stmt)
    assert "INSERT INTO scanner_events" in sql
    assert "ON CONFLICT (event_id) DO NOTHING" in sql  # redelivered Kafka events are no-ops
    db.commit.assert_called_once()
    assert result is stored


def test_get_pending_events_selects_oldest_pending_first():
    db = MagicMock()
    db.execute.return_value.scalars.return_value = iter(["e1", "e2"])

    assert get_pending_events(db, limit=5) == ["e1", "e2"]
    sql = _sql(db.execute.call_args.args[0])
    assert "WHERE scanner_events.status = %(status_1)s" in sql
    assert "ORDER BY scanner_events.created_at" in sql
    assert "LIMIT" in sql


def test_update_event_status_commits_only_when_the_event_exists():
    db = MagicMock()
    event = SimpleNamespace(status="pending")
    db.execute.return_value.scalar_one_or_none.return_value = event
    update_event_status(db, "evt-1", "queued")
    assert event.status == "queued"
    db.commit.assert_called_once()

    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    update_event_status(db, "missing", "queued")
    db.commit.assert_not_called()
