from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware import SecurityHeadersMiddleware
from api.routers import audit, auth, risks

app = FastAPI(
    title="PII Ghost-Hunter API",
    description="Shadow Data & PII detection and remediation platform",
    version="0.1.0",
    # Disable docs in production — re-enable via env var for dev
    docs_url="/docs",
    redoc_url=None,
)

# Security headers on every response
app.add_middleware(SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router)
app.include_router(risks.router)
app.include_router(audit.router)


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "pii-ghost-hunter-api"}
