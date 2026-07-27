from fastapi import APIRouter, Depends, Header, Request
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.openapi import errors
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    firebase_id_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str = Field(description="Short-lived RS256 JWT access token.")
    refresh_token: str = Field(description="Opaque refresh token used to obtain a new access token.")
    token_type: str = Field(default="bearer", description="Token type, always 'bearer'.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIzZmE4NWY2NC01NzE3LTQ1NjItYjNmYy0yYzk2M2Y2NmFmYTYiLCJyb2xlIjoic3R1ZGVudCIsImV4cCI6MTc1MDAwMDAwMH0.signature",
                "refresh_token": "dGhpcyBpcyBhIHJlZnJlc2ggdG9rZW4gZXhhbXBsZQ",
                "token_type": "bearer",
            }
        }
    }


@router.post("/login", response_model=TokenResponse, summary="Exchange Google ID token for a JWT", responses=errors(401, 422, 429))
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    firebase_data = auth_service.verify_firebase_token(body.firebase_id_token)
    user = await auth_service.get_or_create_user(db, firebase_data)
    access_token = auth_service.create_access_token(user)
    refresh_token = await auth_service.create_refresh_token(user.id, request.app.state.redis)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse, summary="Refresh an access token", responses=errors(401, 422, 429))
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    access_token, new_refresh = await auth_service.verify_and_rotate_refresh_token(
        body.refresh_token, request.app.state.redis, db
    )
    return TokenResponse(access_token=access_token, refresh_token=new_refresh)


@router.post("/logout", status_code=204, summary="Revoke all of the caller's refresh tokens", responses=errors(401, 429))
async def logout(
    request: Request,
    authorization: str | None = Header(None),
    current_user: User = Depends(get_current_user),
):
    redis = request.app.state.redis
    await auth_service.revoke_all_refresh_tokens(current_user.id, redis)
    # Also denylist the presented access token's jti so it can't be used for the
    # remainder of its 15-minute TTL after logout.
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
        try:
            payload = jwt.decode(
                token,
                settings.jwt_public_key,
                algorithms=["RS256"],
                audience=auth_service.JWT_AUDIENCE,
                issuer=auth_service.JWT_ISSUER,
            )
        except JWTError:
            payload = None
        if payload is not None:
            await auth_service.add_access_token_to_denylist(payload, redis)
    return None
