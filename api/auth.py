from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = 60

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

# Static dev-only user store. In production, replace with a DB-backed table
# with bcrypt-hashed passwords.
_USERS: dict[str, dict[str, str]] = {
    "admin@company.com": {
        "password": os.environ.get("ADMIN_PASSWORD", "admin"),
        "role": "admin",
        "name": "Admin",
    },
    "dpo@company.com": {
        "password": os.environ.get("DPO_PASSWORD", "dpo"),
        "role": "dpo",
        "name": "Data Protection Officer",
    },
    "auditor@company.com": {
        "password": os.environ.get("AUDITOR_PASSWORD", "auditor"),
        "role": "auditor",
        "name": "Auditor",
    },
    "viewer@company.com": {
        "password": os.environ.get("VIEWER_PASSWORD", "viewer"),
        "role": "viewer",
        "name": "Viewer",
    },
}


def authenticate_user(email: str, password: str) -> dict[str, str] | None:
    user = _USERS.get(email)
    if not user or user["password"] != password:
        return None
    return {"email": email, "role": user["role"], "name": user["name"]}


def create_access_token(data: dict[str, Any]) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict[str, str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str | None = payload.get("sub")
        role: str | None = payload.get("role")
        if not email or not role:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return {"email": email, "role": role, "name": payload.get("name", "")}
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def require_role(*roles: str):
    """Dependency factory that enforces role-based access."""

    async def _checker(user: dict[str, str] = Depends(get_current_user)) -> dict[str, str]:
        if user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return user

    return _checker
