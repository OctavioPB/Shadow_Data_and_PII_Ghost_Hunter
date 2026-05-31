from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ─── Security headers ─────────────────────────────────────────────────────────

_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds hardened HTTP response headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        # HSTS only when request arrived over HTTPS (trusted proxy signal)
        if request.headers.get("X-Forwarded-Proto") == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        return response


# ─── Rate limiting ────────────────────────────────────────────────────────────

_RATE_WINDOW_SECONDS = 60
_AUTH_MAX_ATTEMPTS = 10  # per IP per window
_RISKS_MAX_REQUESTS = 120  # per IP per window (2 req/s burst)

# Sliding-window buckets — keyed by "endpoint:ip"
_buckets: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate(key: str, max_calls: int) -> None:
    now = time.monotonic()
    window_start = now - _RATE_WINDOW_SECONDS
    bucket = _buckets[key]
    # Evict timestamps outside the sliding window
    _buckets[key] = [t for t in bucket if t > window_start]
    if len(_buckets[key]) >= max_calls:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests — try again later.",
            headers={"Retry-After": str(_RATE_WINDOW_SECONDS)},
        )
    _buckets[key].append(now)


def auth_rate_limit(request: Request) -> None:
    """FastAPI dependency: enforces login rate limit per client IP."""
    _check_rate(f"auth:{_client_ip(request)}", _AUTH_MAX_ATTEMPTS)


def api_rate_limit(request: Request) -> None:
    """FastAPI dependency: enforces general API rate limit per client IP."""
    _check_rate(f"api:{_client_ip(request)}", _RISKS_MAX_REQUESTS)
