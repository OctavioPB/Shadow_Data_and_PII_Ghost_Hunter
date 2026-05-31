"""
DPO notification service — S5-03.

Sends a Jinja-templated HTML email and a Slack webhook message when PII
is detected and quarantined.  Both channels are retried up to 3 times
with exponential back-off.  Every delivery attempt (success or failure)
is logged to the `notifications` table.

Privacy:
  - Templates receive only metadata (table_id, categories, confidence, owner).
  - No raw sample values are ever included in notifications.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import time
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
from jinja2 import Environment, FileSystemLoader

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=True,
)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds; doubled on each retry


@dataclass
class NotificationContext:
    table_id: str
    source_name: str
    flagged_categories: list[str]
    max_confidence: float
    owner_email: str | None
    findings: list[dict]  # [{"column_name": str, "pii_category": str, "confidence": float}]
    dashboard_url: str
    detected_at: str
    recommended_action: str = (
        "Review the quarantined data and decide whether to approve anonymization "
        "or release the data with a documented justification."
    )


# ─── Rendering ────────────────────────────────────────────────────────────────

def _render_html_email(ctx: NotificationContext) -> str:
    tpl = _jinja_env.get_template("dpo_email.html.j2")
    return tpl.render(**ctx.__dict__)


def _render_slack_message(ctx: NotificationContext) -> dict:
    categories_text = ", ".join(f"`{c}`" for c in ctx.flagged_categories)
    return {
        "text": f":warning: *PII Alert* — `{ctx.source_name}`",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🔒 PII Ghost-Hunter Alert"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Source:*\n`{ctx.source_name}`"},
                    {"type": "mrkdwn", "text": f"*Table ID:*\n`{ctx.table_id}`"},
                    {"type": "mrkdwn", "text": f"*PII Categories:*\n{categories_text}"},
                    {
                        "type": "mrkdwn",
                        "text": f"*Max Confidence:*\n{ctx.max_confidence * 100:.0f}%",
                    },
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Review in Dashboard"},
                        "url": f"{ctx.dashboard_url}/tables/{ctx.table_id}/pii-report",
                        "style": "primary",
                    }
                ],
            },
        ],
    }


# ─── Delivery with retry ──────────────────────────────────────────────────────

def _with_retry(fn, *args, max_retries: int = _MAX_RETRIES, **kwargs) -> tuple[bool, str]:
    """
    Call *fn(*args, **kwargs)* up to *max_retries* times.
    Returns (success: bool, error_message: str).
    """
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            fn(*args, **kwargs)
            return True, ""
        except Exception as exc:
            last_error = str(exc)
            if attempt < max_retries:
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                log.warning("Delivery attempt %d/%d failed: %s — retrying in %.1fs",
                            attempt, max_retries, exc, delay)
                time.sleep(delay)
            else:
                log.error("Delivery failed after %d attempts: %s", max_retries, exc)
    return False, last_error


def _send_email(
    recipient: str,
    subject: str,
    html_body: str,
    smtp_host: str,
    smtp_port: int,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = "pii-hunter@noreply.internal"
    msg["To"] = recipient
    msg.set_content("PII Alert — please view this email in an HTML-capable client.")
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        smtp.send_message(msg)


def _send_slack(webhook_url: str, payload: dict) -> None:
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(webhook_url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Slack webhook returned {resp.status_code}: {resp.text[:200]}")


# ─── DB persistence ───────────────────────────────────────────────────────────

def _record_notification(
    engine,
    notification_type: str,
    recipient: str,
    subject: str,
    table_id: str,
    status: str,
    retry_count: int,
    error_message: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                """
                INSERT INTO notifications
                    (id, notification_type, recipient, subject, table_id,
                     status, retry_count, error_message, sent_at)
                VALUES
                    (:id, :ntype, :recipient, :subject, :tid,
                     :status, :retries, :error, now())
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "ntype": notification_type,
                "recipient": recipient,
                "subject": subject,
                "tid": table_id,
                "status": status,
                "retries": retry_count,
                "error": error_message or None,
            },
        )


# ─── Public interface ─────────────────────────────────────────────────────────

def send_dpo_notifications(
    ctx: NotificationContext,
    database_url: str,
    dpo_email: str = "",
    slack_webhook_url: str = "",
    smtp_host: str = "",
    smtp_port: int = 587,
) -> dict[str, Any]:
    """
    Send email and/or Slack notification to the DPO.
    Each channel is retried independently; failures do not block the other.
    Returns a summary of delivery outcomes.
    """
    engine = sa.create_engine(database_url)
    results: dict[str, Any] = {"table_id": ctx.table_id, "email": None, "slack": None}
    subject = f"[PII Alert] High-confidence PII detected: {ctx.source_name}"

    # ── Email ─────────────────────────────────────────────────────────────────
    if dpo_email and smtp_host:
        html_body = _render_html_email(ctx)
        success, error = _with_retry(
            _send_email, dpo_email, subject, html_body, smtp_host, smtp_port
        )
        status = "sent" if success else "failed"
        _record_notification(
            engine, "email", dpo_email, subject, ctx.table_id,
            status, _MAX_RETRIES if not success else 1, error,
        )
        results["email"] = {"status": status, "recipient": dpo_email}
        log.info("Email notification %s to %s", status, dpo_email)

    # ── Slack ─────────────────────────────────────────────────────────────────
    if slack_webhook_url:
        slack_payload = _render_slack_message(ctx)
        success, error = _with_retry(_send_slack, slack_webhook_url, slack_payload)
        status = "sent" if success else "failed"
        _record_notification(
            engine, "slack", slack_webhook_url[:64], subject, ctx.table_id,
            status, _MAX_RETRIES if not success else 1, error,
        )
        results["slack"] = {"status": status}
        log.info("Slack notification %s", status)

    return results
