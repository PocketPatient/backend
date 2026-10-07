from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from firebase_admin import auth as firebase_auth
from jose import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User, UserRole

_ACCESS_TOKEN_EXPIRE_MINUTES = 15
_REFRESH_TOKEN_EXPIRE_SECONDS = 7 * 24 * 60 * 60
# Fixed JWT issuer/audience. Minted into every access token and required at verify
# time so a stray RS256 token signed with the same key (but for a different
# service/audience) cannot authenticate against this API.
JWT_ISSUER = "pocketpatient-api"
JWT_AUDIENCE = "pocketpatient-app"
_RUTGERS_DOMAINS = ("@scarletmail.rutgers.edu", "@rutgers.edu")
# Extra domains allowed in local dev (never reaches production — seed script only)
_DEV_TEST_DOMAINS = ("@test.pocketpatient.dev",)


def verify_firebase_token(id_token: str) -> dict:
    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Firebase token")
    email: str = decoded.get("email") or ""
    if not email:
        # users.email is NOT NULL; e.g. Apple without the email scope.
        raise HTTPException(status_code=403, detail="Sign-in did not provide an email address")
    if not settings.allow_non_rutgers_accounts:
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
        # Optional for every provider: Apple only shares the name on first
        # authorization (and Firebase only has it if the client set it).
        "name": decoded.get("name") or None,
        "sign_in_provider": decoded.get("firebase", {}).get("sign_in_provider"),
    }


async def _get_user_by_uid(db: AsyncSession, uid: str) -> User | None:
    result = await db.execute(select(User).where(User.google_uid == uid))
    return result.scalar_one_or_none()


async def get_or_create_user(db: AsyncSession, firebase_data: dict) -> User:
    # Identity is the stable Firebase UID (stored in the legacy-named google_uid
    # column) for every provider — never the email.
    user = await _get_user_by_uid(db, firebase_data["uid"])
    if user is None:
        user = User(
            google_uid=firebase_data["uid"],
            email=firebase_data["email"],
            display_name=firebase_data.get("name"),
            role=None,
            is_verified=None,
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            # Concurrent first login for the same UID: the other request won.
            user = await _get_user_by_uid(db, firebase_data["uid"])
            if user is not None:
                return user
            # Otherwise the email belongs to a different Firebase UID, e.g. the
            # same address used with another sign-in provider while Firebase
            # account linking is off. Fail cleanly instead of a 500.
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists under a different sign-in method",
            )
        await db.refresh(user)
    return user


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value if user.role else None,
        "jti": str(uuid.uuid4()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
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


async def revoke_all_refresh_tokens(user_id: uuid.UUID, redis) -> None:
    set_key = f"refresh_user:{user_id}"
    hashes = await redis.smembers(set_key)
    keys = [f"refresh:{token_hash}" for token_hash in hashes]
    if keys:
        await redis.delete(*keys)
    await redis.delete(set_key)


async def verify_and_rotate_refresh_token(
    token: str, redis, db: AsyncSession
) -> tuple[str, str]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    key = f"refresh:{token_hash}"
    user_id_str = await redis.getdel(key)
    if not user_id_str:
        # The token is not an active refresh token. If we still have a record that
        # it was previously consumed (rotated), this is a replay of an already-used
        # token — a classic stolen-token signal. Revoke the entire family so the
        # attacker's rotated chain is invalidated too.
        consumed_owner = await redis.get(f"refresh_consumed:{token_hash}")
        if consumed_owner:
            await revoke_all_refresh_tokens(uuid.UUID(consumed_owner), redis)
            raise HTTPException(status_code=401, detail="Refresh token reuse detected")
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")
    await redis.srem(f"refresh_user:{user_id_str}", token_hash)
    # Remember that this hash was consumed so a later replay triggers family revocation.
    await redis.setex(
        f"refresh_consumed:{token_hash}", _REFRESH_TOKEN_EXPIRE_SECONDS, user_id_str
    )
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id_str)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    access_token = create_access_token(user)
    new_refresh_token = await create_refresh_token(user.id, redis)
    return access_token, new_refresh_token


async def add_access_token_to_denylist(payload: dict, redis) -> None:
    """Denylist an access token's ``jti`` until its natural expiry.

    Called on logout so a still-unexpired access token cannot be used after the
    user logs out. TTL is the token's remaining lifetime; once it would have
    expired anyway the denylist entry is reaped automatically.
    """
    jti = payload.get("jti")
    if not jti:
        return
    exp = payload.get("exp")
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = int(exp) - now if exp is not None else _ACCESS_TOKEN_EXPIRE_MINUTES * 60
    if ttl <= 0:
        return
    await redis.setex(f"denylist:{jti}", ttl, "1")


async def is_access_token_denylisted(jti: str, redis) -> bool:
    """True if the given access-token ``jti`` was denylisted (e.g. via logout)."""
    if not jti:
        return False
    return bool(await redis.exists(f"denylist:{jti}"))


async def mark_professor_verified(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Grant verified-professor status to a user.

    NOT self-service: this is the ONLY sanctioned way ``is_verified`` becomes True
    for a professor, and it is intended to be invoked exclusively from an
    out-of-band admin/review path (an admin CLI or an admin-only endpoint gated by
    a different role) — never from a request authenticated as the user being
    verified. The self-service ``PUT /users/me/role`` deliberately sets
    ``is_verified=False`` for professors, so a user cannot unilaterally grant
    themselves verified-professor access (course creation, disease-document upload,
    professor analytics / FERPA data).
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.role != UserRole.professor:
        return None
    user.is_verified = True
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
