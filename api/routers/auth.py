from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from api.auth import authenticate_user, create_access_token, get_current_user
from api.middleware import auth_rate_limit
from api.schemas.auth import MeResponse, TokenResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse, dependencies=[Depends(auth_rate_limit)])
async def login(form_data: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        {"sub": user["email"], "role": user["role"], "name": user["name"]}
    )
    return TokenResponse(access_token=token, role=user["role"], name=user["name"])


@router.get("/me", response_model=MeResponse)
async def me(user: dict[str, str] = Depends(get_current_user)) -> MeResponse:
    return MeResponse(email=user["email"], role=user["role"], name=user["name"])
