"""
Locust load test — [S7-02]

Targets:
  - API backend (FastAPI, port 8000): p95 < 500ms
  - PII inference service (FastAPI, port 8001): p95 < 2s

Usage:
  # Against local docker-compose stack:
  locust -f tests/perf/locustfile.py --host http://localhost:8000 \
         --users 500 --spawn-rate 10 --run-time 10m --headless

  # Inference service only:
  locust -f tests/perf/locustfile.py::InferenceUser \
         --host http://localhost:8001 \
         --users 100 --spawn-rate 5 --run-time 10m --headless

  # With HTML report:
  locust -f tests/perf/locustfile.py --host http://localhost:8000 \
         --users 500 --spawn-rate 10 --run-time 10m --headless \
         --html tests/perf/report.html

Performance targets (from PLAN.md S7-02):
  - API p95 latency < 500ms
  - Inference p95 latency < 2s
"""

from __future__ import annotations

import json
import os
import random
import string

from locust import HttpUser, between, constant, events, task

# ─── Configuration ────────────────────────────────────────────────────────────

_DPO_EMAIL = os.environ.get("PERF_DPO_EMAIL", "dpo@company.com")
_DPO_PASSWORD = os.environ.get("PERF_DPO_PASSWORD", "dpo")
_VIEWER_EMAIL = os.environ.get("PERF_VIEWER_EMAIL", "viewer@company.com")
_VIEWER_PASSWORD = os.environ.get("PERF_VIEWER_PASSWORD", "viewer")

_SAMPLE_TABLE_IDS = [
    "tbl-prod-customers-001",
    "tbl-staging-users-042",
    "tbl-analytics-events-007",
    "tbl-backup-orders-2024",
    "tbl-temp-migration-pii",
]

_PII_CATEGORIES = ["EMAIL", "SSN", "CREDIT_CARD", "PHONE", "FULL_NAME"]


# ─── Helper: acquire JWT token ────────────────────────────────────────────────


def _get_token(client, email: str, password: str) -> str:
    resp = client.post(
        "/api/v1/auth/token",
        data={"username": email, "password": password},
        name="/auth/token [warmup]",
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ─── API Backend User ─────────────────────────────────────────────────────────


class APIUser(HttpUser):
    """Simulates a mix of DPO and viewer users hitting the dashboard API.

    Wait time: uniform 0.5–2s between tasks (think-time model).
    Target: p95 < 500ms for all GET endpoints.
    """

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        self._token = _get_token(self.client, _VIEWER_EMAIL, _VIEWER_PASSWORD)
        self._headers = {"Authorization": f"Bearer {self._token}"}

    @task(5)
    def get_risks(self) -> None:
        self.client.get(
            "/api/v1/risks?page=1&size=20",
            headers=self._headers,
            name="/api/v1/risks",
        )

    @task(3)
    def get_risks_filtered(self) -> None:
        cat = random.choice(_PII_CATEGORIES)
        self.client.get(
            f"/api/v1/risks?pii_category={cat}&page=1&size=20",
            headers=self._headers,
            name="/api/v1/risks [filtered]",
        )

    @task(4)
    def get_stats_summary(self) -> None:
        self.client.get(
            "/api/v1/stats/summary",
            headers=self._headers,
            name="/api/v1/stats/summary",
        )

    @task(2)
    def get_pii_report(self) -> None:
        table_id = random.choice(_SAMPLE_TABLE_IDS)
        self.client.get(
            f"/api/v1/tables/{table_id}/pii-report",
            headers=self._headers,
            name="/api/v1/tables/{id}/pii-report",
        )

    @task(2)
    def get_audit_log(self) -> None:
        self.client.get(
            "/api/v1/audit-log?size=50",
            headers=self._headers,
            name="/api/v1/audit-log",
        )

    @task(1)
    def get_data_sources(self) -> None:
        self.client.get(
            "/api/v1/data-sources",
            headers=self._headers,
            name="/api/v1/data-sources",
        )

    @task(1)
    def health_check(self) -> None:
        self.client.get("/health", name="/health")


class DPOUser(HttpUser):
    """DPO users: heavier read pattern + occasional remediation POST.

    Remediation posts are rare (1:20 ratio vs. reads) to simulate real DPO behaviour.
    """

    wait_time = between(1.0, 4.0)

    def on_start(self) -> None:
        self._token = _get_token(self.client, _DPO_EMAIL, _DPO_PASSWORD)
        self._headers = {"Authorization": f"Bearer {self._token}"}

    @task(10)
    def browse_inventory(self) -> None:
        self.client.get(
            "/api/v1/risks?page=1&size=20",
            headers=self._headers,
            name="/api/v1/risks [dpo]",
        )

    @task(5)
    def review_pii_report(self) -> None:
        table_id = random.choice(_SAMPLE_TABLE_IDS)
        self.client.get(
            f"/api/v1/tables/{table_id}/pii-report",
            headers=self._headers,
            name="/api/v1/tables/{id}/pii-report [dpo]",
        )

    @task(1)
    def trigger_remediation(self) -> None:
        table_id = random.choice(_SAMPLE_TABLE_IDS)
        action = random.choice(["anonymize", "quarantine"])
        self.client.post(
            f"/api/v1/tables/{table_id}/remediate",
            json={"action": action, "notes": "load test"},
            headers=self._headers,
            name="/api/v1/tables/{id}/remediate [dpo]",
        )


# ─── Inference Service User ───────────────────────────────────────────────────


class InferenceUser(HttpUser):
    """Simulates Airflow operators calling the inference service.

    Wait time: constant 0.1s — inference is called serially per table column batch.
    Target: p95 < 2s.
    Host: set to http://localhost:8001 when running inference-only load test.
    """

    wait_time = constant(0.1)

    @task(8)
    def infer_columns(self) -> None:
        payload = {
            "table_id": f"tbl-load-{random.randint(1, 9999):04d}",
            "columns": [
                {
                    "column_id": f"col-{i}",
                    "column_name": random.choice(
                        ["email_address", "ssn", "phone_number", "full_name", "created_at"]
                    ),
                    "values": [
                        "".join(random.choices(string.ascii_letters, k=10))
                        for _ in range(10)
                    ],
                }
                for i in range(random.randint(1, 5))
            ],
        }
        self.client.post(
            "/infer",
            json=payload,
            name="/infer",
        )

    @task(2)
    def health_check(self) -> None:
        self.client.get("/health", name="/health [inference]")


# ─── SLA assertion listener ───────────────────────────────────────────────────


@events.quitting.add_listener
def assert_sla(environment, **kwargs) -> None:
    """Fail the Locust run if p95 targets are breached."""
    stats = environment.runner.stats
    failed = False

    api_endpoints = [
        "/api/v1/risks",
        "/api/v1/stats/summary",
        "/api/v1/audit-log",
    ]

    for name in api_endpoints:
        entry = stats.entries.get((name, "GET"))
        if entry is None:
            continue
        p95_ms = entry.get_response_time_percentile(0.95)
        if p95_ms is not None and p95_ms > 500:
            print(f"[SLA BREACH] {name} p95={p95_ms:.0f}ms > 500ms target")
            failed = True
        else:
            print(f"[SLA OK]     {name} p95={p95_ms:.0f}ms")

    infer_entry = stats.entries.get(("/infer", "POST"))
    if infer_entry is not None:
        p95_ms = infer_entry.get_response_time_percentile(0.95)
        if p95_ms is not None and p95_ms > 2000:
            print(f"[SLA BREACH] /infer p95={p95_ms:.0f}ms > 2000ms target")
            failed = True
        else:
            print(f"[SLA OK]     /infer p95={p95_ms:.0f}ms")

    if failed:
        environment.process_exit_code = 1
