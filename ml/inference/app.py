"""
PII Inference Service — FastAPI microservice.

Endpoints:
  POST /infer        — classify up to 50 columns from a single table
  GET  /health       — liveness + model-loaded flag
  GET  /metrics      — Prometheus metrics

Privacy guarantee (S4-04):
  - Raw sample values from the request payload are NEVER logged.
  - Structured log entries include only: table_id, column_id, pii_category, confidence.

Run:
  uvicorn ml.inference.app:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import logging
import os
import time

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ml.inference.metrics import (
    FLAGGED_COLUMNS,
    INFERENCE_COLUMNS,
    INFERENCE_LATENCY,
    INFERENCE_REQUESTS,
    MODEL_LOAD_DURATION,
)
from ml.inference.model_loader import (
    CONFIDENCE_THRESHOLD,
    classify_column,
    is_loaded,
    load_duration_seconds,
)
from ml.inference.schemas import (
    ColumnResult,
    HealthResponse,
    InferRequest,
    InferResponse,
)

# ─── Logging (structlog — privacy-safe) ───────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger()

# ─── Application ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="PII Inference Service",
    description="Classifies column samples for PII using a fine-tuned DistilBERT model.",
    version=os.environ.get("MODEL_VERSION", "dev"),
    # Disable default /docs to avoid exposing sample values in Swagger UI
    docs_url=None,
    redoc_url=None,
)


@app.on_event("startup")
async def _warmup_model() -> None:
    """Eagerly load the model so the first request is not slow."""
    from ml.inference.model_loader import get_pipeline

    try:
        get_pipeline()
        duration = load_duration_seconds()
        if duration is not None:
            MODEL_LOAD_DURATION.observe(duration)
        log.info("model_loaded", duration_s=round(duration or 0, 2))
    except Exception as exc:
        log.warning("model_load_failed", error=str(exc))


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model_loaded=is_loaded(),
        version=os.environ.get("MODEL_VERSION"),
    )


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/infer", response_model=InferResponse)
async def infer(request: InferRequest) -> InferResponse:
    t_start = time.monotonic()
    status = "success"
    results: list[ColumnResult] = []

    try:
        for col in request.columns:
            pii_category, confidence = classify_column(
                column_name=col.column_name,
                values=col.values,
            )
            flagged = pii_category != "NONE" and confidence >= CONFIDENCE_THRESHOLD

            # S4-04: log only metadata — never the raw values
            log.info(
                "column_classified",
                table_id=request.table_id,
                column_id=col.column_id,
                pii_category=pii_category,
                confidence=confidence,
                flagged=flagged,
            )

            INFERENCE_COLUMNS.labels(pii_category=pii_category).inc()
            if flagged:
                FLAGGED_COLUMNS.labels(pii_category=pii_category).inc()

            results.append(
                ColumnResult(
                    column_id=col.column_id,
                    pii_category=pii_category,
                    confidence=confidence,
                    flagged=flagged,
                )
            )
    except Exception as exc:
        status = "error"
        log.error("inference_error", table_id=request.table_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Inference failed") from exc
    finally:
        duration = time.monotonic() - t_start
        INFERENCE_LATENCY.labels(status=status).observe(duration)
        INFERENCE_REQUESTS.labels(status=status).inc()

    log.info(
        "inference_complete",
        table_id=request.table_id,
        column_count=len(results),
        flagged_count=sum(1 for r in results if r.flagged),
        duration_s=round(time.monotonic() - t_start, 3),
    )

    return InferResponse(table_id=request.table_id, results=results)
