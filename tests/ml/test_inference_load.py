"""
Load test for the PII inference service — S4-05.

Sends 100 concurrent /infer requests and asserts p95 latency < 2 s.

The model is mocked — this tests HTTP layer overhead + async throughput,
not actual model inference speed.

Run standalone: pytest -m "not integration" tests/ml/test_inference_load.py -v
"""

from __future__ import annotations

import asyncio
import statistics
import time
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def base_url():
    """Start the FastAPI app in a background thread and return its URL."""
    with (
        patch("ml.inference.model_loader.get_pipeline"),
        patch("ml.inference.model_loader.is_loaded", return_value=True),
        patch("ml.inference.model_loader.load_duration_seconds", return_value=0.5),
        patch("ml.inference.app.classify_column", return_value=("NONE", 0.55)),
    ):
        from ml.inference.app import app
        import threading
        import uvicorn

        config = uvicorn.Config(app, host="127.0.0.1", port=18001, log_level="critical")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Wait for server to be ready
        for _ in range(30):
            try:
                httpx.get("http://127.0.0.1:18001/health", timeout=1.0)
                break
            except Exception:
                time.sleep(0.1)

        yield "http://127.0.0.1:18001"
        server.should_exit = True


def _make_payload(table_id: str = "tbl-load") -> dict:
    return {
        "table_id": table_id,
        "columns": [
            {"column_id": f"col-{i}", "column_name": "email", "values": ["a@b.com"]}
            for i in range(5)
        ],
    }


async def _send_request(client: httpx.AsyncClient, url: str, idx: int) -> float:
    """Send one /infer request and return the elapsed time in seconds."""
    t0 = time.monotonic()
    resp = await client.post(
        f"{url}/infer",
        json=_make_payload(f"tbl-load-{idx}"),
        timeout=10.0,
    )
    elapsed = time.monotonic() - t0
    assert resp.status_code == 200, f"Request {idx} failed: {resp.status_code}"
    return elapsed


@pytest.mark.asyncio
async def test_100_concurrent_requests_p95_under_2s(base_url):
    """100 simultaneous /infer requests — p95 latency must be < 2 s."""
    n = 100
    async with httpx.AsyncClient() as client:
        latencies = await asyncio.gather(
            *[_send_request(client, base_url, i) for i in range(n)]
        )

    latencies_sorted = sorted(latencies)
    p50 = latencies_sorted[int(n * 0.50)]
    p95 = latencies_sorted[int(n * 0.95)]
    p99 = latencies_sorted[int(n * 0.99)]
    mean = statistics.mean(latencies)

    print(
        f"\nLoad test results ({n} concurrent requests):\n"
        f"  mean={mean:.3f}s  p50={p50:.3f}s  p95={p95:.3f}s  p99={p99:.3f}s"
    )

    assert p95 < 2.0, f"p95 latency {p95:.3f}s exceeded 2 s SLO"


@pytest.mark.asyncio
async def test_throughput_minimum_rps(base_url):
    """50 concurrent requests should complete in under 10 s (≥5 rps)."""
    n = 50
    t_start = time.monotonic()
    async with httpx.AsyncClient() as client:
        await asyncio.gather(
            *[_send_request(client, base_url, i) for i in range(n)]
        )
    total = time.monotonic() - t_start
    rps = n / total
    print(f"\nThroughput: {rps:.1f} rps over {total:.2f} s")
    assert total < 10.0, f"50 requests took {total:.2f} s — throughput too low"
