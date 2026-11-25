"""Test doubles for the async SQLAlchemy session used by the API routers.

FakeSession returns queued results in call order and records every executed
statement and its parameters, so tests can assert what was written (e.g. the
audit-log payload), not just the HTTP status.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from api.auth import get_current_user
from api.db import get_db
from api.main import app

DPO = {"email": "dpo@company.com", "role": "dpo", "name": "DPO"}
VIEWER = {"email": "viewer@company.com", "role": "viewer", "name": "Viewer"}


def row(**fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**fields)


class Result:
    def __init__(self, rows: list | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self) -> list:
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class FakeSession:
    def __init__(self, *results: Result) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, Any]] = []
        self.committed = False

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return self._results.pop(0) if self._results else Result()

    async def commit(self) -> None:
        self.committed = True

    def sql_containing(self, fragment: str) -> list[tuple[str, Any]]:
        return [c for c in self.calls if fragment in c[0]]

    def audit_details(self) -> list[dict]:
        """details_json of every audit_log INSERT (handles executemany lists)."""
        out = []
        for _sql, params in self.sql_containing("INSERT INTO audit_log"):
            for p in params if isinstance(params, list) else [params]:
                raw = next(v for k, v in p.items() if k in ("details", "d"))
                out.append(json.loads(raw))
        return out


def use_session(session: FakeSession) -> FakeSession:
    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return session


def login_as(user: dict) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


def reset() -> None:
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
