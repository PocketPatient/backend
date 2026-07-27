from __future__ import annotations

import uuid
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.services.auth_service import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    is_access_token_denylisted,
)


async def get_current_user(
    request: Request = None,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(
            token,
            settings.jwt_public_key,
            algorithms=["RS256"],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
        )
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # Reject tokens whose jti was denylisted on logout (still within their TTL).
    redis = getattr(getattr(request, "app", None), "state", None)
    redis = getattr(redis, "redis", None) if redis is not None else None
    if redis is not None and await is_access_token_denylisted(payload.get("jti"), redis):
        raise HTTPException(status_code=401, detail="Token has been revoked")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def require_role(role: str) -> Callable:
    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role is None or current_user.role.value != role:
            raise HTTPException(status_code=403, detail="Insufficient role")
        # Verified-professor gate: a self-assigned professor has is_verified=False
        # (set by PUT /users/me/role) and must not reach professor-only surfaces
        # (course creation, disease-document upload, professor analytics / FERPA).
        # is_verified only becomes True via auth_service.mark_professor_verified,
        # an out-of-band admin path — never self-service.
        if role == "professor" and current_user.is_verified is not True:
            raise HTTPException(status_code=403, detail="Professor account not verified")
        return current_user
    return dependency
