import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware import SecurityHeadersMiddleware
from api.routers import audit, auth, compliance, dsar, risks
from api.routers import demo as _demo_router

_DOCS_ENABLED = os.environ.get("API_DOCS_ENABLED", "true").lower() in ("1", "true", "yes")
_CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

app = FastAPI(
    title="PII Ghost-Hunter API",
    description="Shadow Data & PII detection and remediation platform",
    version="0.1.0",
    # Set API_DOCS_ENABLED=false in production to hide the OpenAPI UI.
    docs_url="/docs" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
    redoc_url=None,
)

# Security headers on every response
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    # PATCH is required by the ROPA annotation endpoint.
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(risks.router)
app.include_router(audit.router)
app.include_router(compliance.router)
app.include_router(dsar.router)

# Demo router is always mounted so the route exists and CORS headers are
# present on every response. The endpoint itself gates on DEMO_MODE at
# request time, returning 403 when demo mode is off.
app.include_router(_demo_router.router)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "pii-ghost-hunter-api"}
