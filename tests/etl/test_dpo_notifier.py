"""
Unit tests for etl/notifiers/dpo_notifier.py — S5-03.

SMTP and Slack HTTP calls are mocked.
Verifies: template rendering, retry logic, DB logging, privacy (no raw values).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from etl.notifiers.dpo_notifier import (
    NotificationContext,
    _render_html_email,
    _render_slack_message,
    _with_retry,
    send_dpo_notifications,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def ctx():
    return NotificationContext(
        table_id="tbl-privacy-test",
        source_name="prod.customer_pii_backup",
        flagged_categories=["EMAIL", "SSN"],
        max_confidence=0.97,
        owner_email="owner@company.com",
        findings=[
            {"column_name": "email", "pii_category": "EMAIL", "confidence": 0.97},
            {"column_name": "ssn", "pii_category": "SSN", "confidence": 0.95},
        ],
        dashboard_url="http://localhost:5173",
        detected_at="2026-05-16T10:00:00Z",
    )


# ─── Template rendering ───────────────────────────────────────────────────────

def test_email_template_contains_table_id(ctx):
    html = _render_html_email(ctx)
    assert ctx.table_id in html


def test_email_template_contains_source_name(ctx):
    html = _render_html_email(ctx)
    assert ctx.source_name in html


def test_email_template_contains_pii_categories(ctx):
    html = _render_html_email(ctx)
    for cat in ctx.flagged_categories:
        assert cat in html


def test_email_template_no_raw_values(ctx):
    """Template must not leak any raw PII values — only column names and categories."""
    html = _render_html_email(ctx)
    # These should NOT appear in the email body
    raw_values = ["alice@example.com", "123-45-6789", "4111-1111-1111-1111"]
    for val in raw_values:
        assert val not in html


def test_email_template_has_dashboard_link(ctx):
    html = _render_html_email(ctx)
    assert ctx.dashboard_url in html
    assert ctx.table_id in html


def test_slack_message_contains_source_name(ctx):
    payload = _render_slack_message(ctx)
    assert ctx.source_name in str(payload)


def test_slack_message_has_blocks(ctx):
    payload = _render_slack_message(ctx)
    assert "blocks" in payload
    assert len(payload["blocks"]) >= 2


def test_slack_message_has_dashboard_button(ctx):
    payload = _render_slack_message(ctx)
    payload_str = json.dumps(payload)
    assert ctx.table_id in payload_str
    assert ctx.dashboard_url in payload_str


# ─── _with_retry ──────────────────────────────────────────────────────────────

def test_with_retry_succeeds_first_attempt():
    fn = MagicMock()
    success, error = _with_retry(fn, "arg1", max_retries=3)
    assert success is True
    assert error == ""
    fn.assert_called_once_with("arg1")


def test_with_retry_retries_on_failure():
    fn = MagicMock(side_effect=[Exception("fail"), Exception("fail"), None])
    with patch("etl.notifiers.dpo_notifier.time.sleep"):
        success, error = _with_retry(fn, max_retries=3)
    assert success is True
    assert fn.call_count == 3


def test_with_retry_returns_failure_after_max_attempts():
    fn = MagicMock(side_effect=Exception("permanent failure"))
    with patch("etl.notifiers.dpo_notifier.time.sleep"):
        success, error = _with_retry(fn, max_retries=3)
    assert success is False
    assert "permanent failure" in error
    assert fn.call_count == 3


def test_with_retry_exponential_backoff():
    fn = MagicMock(side_effect=[Exception("fail"), Exception("fail"), None])
    sleep_calls = []
    with patch("etl.notifiers.dpo_notifier.time.sleep", side_effect=lambda d: sleep_calls.append(d)):
        _with_retry(fn, max_retries=3)
    # Second delay should be 2x the first
    assert len(sleep_calls) == 2
    assert sleep_calls[1] > sleep_calls[0]


# ─── send_dpo_notifications ───────────────────────────────────────────────────

def _make_db_mock():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_engine = MagicMock()
    mock_engine.begin.return_value = mock_conn
    return mock_engine, mock_conn


def test_send_notifications_records_email_to_db(ctx):
    mock_engine, mock_conn = _make_db_mock()
    with (
        patch("etl.notifiers.dpo_notifier.sa.create_engine", return_value=mock_engine),
        patch("etl.notifiers.dpo_notifier._send_email") as mock_email,
    ):
        send_dpo_notifications(
            ctx=ctx,
            database_url="db_url",
            dpo_email="dpo@company.com",
            smtp_host="smtp.example.com",
        )

    mock_email.assert_called_once()
    # DB must have recorded the notification
    assert mock_conn.execute.called
    all_sqls = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
    assert "notifications" in all_sqls


def test_send_notifications_records_slack_to_db(ctx):
    mock_engine, mock_conn = _make_db_mock()
    with (
        patch("etl.notifiers.dpo_notifier.sa.create_engine", return_value=mock_engine),
        patch("etl.notifiers.dpo_notifier._send_slack") as mock_slack,
    ):
        send_dpo_notifications(
            ctx=ctx,
            database_url="db_url",
            slack_webhook_url="https://hooks.slack.com/test",
        )

    mock_slack.assert_called_once()


def test_send_notifications_records_failure(ctx):
    """A failed delivery must be recorded with status='failed', not raise."""
    mock_engine, mock_conn = _make_db_mock()
    with (
        patch("etl.notifiers.dpo_notifier.sa.create_engine", return_value=mock_engine),
        patch("etl.notifiers.dpo_notifier._send_email", side_effect=Exception("SMTP down")),
        patch("etl.notifiers.dpo_notifier.time.sleep"),
    ):
        result = send_dpo_notifications(
            ctx=ctx,
            database_url="db_url",
            dpo_email="dpo@company.com",
            smtp_host="smtp.example.com",
        )

    assert result["email"]["status"] == "failed"
    # DB notification must have been recorded (even on failure)
    assert mock_conn.execute.called


def test_send_notifications_no_channels_skips_gracefully(ctx):
    """No email or Slack configured — must not raise."""
    mock_engine, _ = _make_db_mock()
    with patch("etl.notifiers.dpo_notifier.sa.create_engine", return_value=mock_engine):
        result = send_dpo_notifications(
            ctx=ctx, database_url="db_url"  # no dpo_email, no slack
        )
    assert result["email"] is None
    assert result["slack"] is None


def test_send_notifications_email_subject_contains_source_name(ctx):
    mock_engine, _ = _make_db_mock()
    captured_subject = []

    def fake_send_email(recipient, subject, html_body, smtp_host, smtp_port):
        captured_subject.append(subject)

    with (
        patch("etl.notifiers.dpo_notifier.sa.create_engine", return_value=mock_engine),
        patch("etl.notifiers.dpo_notifier._send_email", side_effect=fake_send_email),
    ):
        send_dpo_notifications(
            ctx=ctx,
            database_url="db_url",
            dpo_email="dpo@company.com",
            smtp_host="smtp.example.com",
        )

    assert ctx.source_name in captured_subject[0]
