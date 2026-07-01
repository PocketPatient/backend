from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from firebase_admin import auth as firebase_auth
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

_ACCESS_TOKEN_EXPIRE_MINUTES = 15
_REFRESH_TOKEN_EXPIRE_SECONDS = 7 * 24 * 60 * 60
_RUTGERS_DOMAINS = ("@scarletmail.rutgers.edu", "@rutgers.edu")
# Extra domains allowed in local dev (never reaches production — seed script only)
_DEV_TEST_DOMAINS = ("@test.pocketpatient.dev",)


def verify_firebase_token(id_token: str) -> dict:
    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Firebase token")
    email: str = decoded.get("email", "")
    allowed = _RUTGERS_DOMAINS + (
        _DEV_TEST_DOMAINS if settings.allow_test_accounts else ()
    )
    if not any(email.endswith(domain) for domain in allowed):
        raise HTTPException(status_code=403, detail="Must use a Rutgers email address")
    if not decoded.get("email_verified", False):
        raise HTTPException(status_code=403, detail="Email address not verified")
    return {
        "uid": decoded["uid"],
        "email": email,
        "name": decoded.get("name"),
        "sign_in_provider": decoded.get("firebase", {}).get("sign_in_provider"),
    }


async def get_or_create_user(db: AsyncSession, firebase_data: dict) -> User:
    result = await db.execute(select(User).where(User.google_uid == firebase_data["uid"]))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            google_uid=firebase_data["uid"],
            email=firebase_data["email"],
            display_name=firebase_data.get("name"),
            role=None,
            is_verified=None,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value if user.role else None,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")


async def create_refresh_token(user_id: uuid.UUID, redis) -> str:
    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await redis.setex(f"refresh:{token_hash}", _REFRESH_TOKEN_EXPIRE_SECONDS, str(user_id))
    await redis.sadd(f"refresh_user:{user_id}", token_hash)
    await redis.expire(f"refresh_user:{user_id}", _REFRESH_TOKEN_EXPIRE_SECONDS)
    return raw_token


async def verify_and_rotate_refresh_token(
    token: str, redis, db: AsyncSession
) -> tuple[str, str]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    key = f"refresh:{token_hash}"
    user_id_str = await redis.getdel(key)
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")
    await redis.srem(f"refresh_user:{user_id_str}", token_hash)
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id_str)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    access_token = create_access_token(user)
    new_refresh_token = await create_refresh_token(user.id, redis)
    return access_token, new_refresh_token
