from __future__ import annotations

from typing import Callable

from fastapi import Depends, Header, HTTPException
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.jwt_public_key, algorithms=["RS256"])
        user_id: str = payload["sub"]
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_role(role: str) -> Callable:
    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role is None or current_user.role.value != role:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return current_user
    return dependency
