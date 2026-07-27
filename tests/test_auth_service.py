import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
from fastapi import HTTPException
from jose import jwt

from app.models.user import User, UserRole
from app.services.auth_service import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    add_access_token_to_denylist,
    create_access_token,
    create_refresh_token,
    get_or_create_user,
    is_access_token_denylisted,
    mark_professor_verified,
    verify_and_rotate_refresh_token,
    verify_firebase_token,
)


def _decode(token, public_pem):
    return jwt.decode(
        token, public_pem, algorithms=["RS256"], audience=JWT_AUDIENCE, issuer=JWT_ISSUER
    )

_TEST_PRIV = _rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


# ── verify_firebase_token ──────────────────────────────────────────────────────

def test_verify_firebase_token_scarletmail():
    decoded = {
        "uid": "uid1",
        "email": "student@scarletmail.rutgers.edu",
        "email_verified": True,
        "name": "Alice",
        "firebase": {"sign_in_provider": "google.com"},
    }
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded):
        result = verify_firebase_token("valid-token")
    assert result["uid"] == "uid1"
    assert result["email"] == "student@scarletmail.rutgers.edu"
    assert result["sign_in_provider"] == "google.com"


def test_verify_firebase_token_rutgers_edu():
    decoded = {
        "uid": "uid2",
        "email": "prof@rutgers.edu",
        "email_verified": True,
        "name": "Bob",
        "firebase": {"sign_in_provider": "password"},
    }
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded):
        result = verify_firebase_token("valid-token")
    assert result["email"] == "prof@rutgers.edu"


def test_verify_firebase_token_non_rutgers_raises_403():
    decoded = {"uid": "uid3", "email": "test@gmail.com", "firebase": {"sign_in_provider": "google.com"}}
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded):
        with pytest.raises(HTTPException) as exc:
            verify_firebase_token("valid-token")
    assert exc.value.status_code == 403


def test_verify_firebase_token_unverified_email_raises_403():
    decoded = {
        "uid": "uid4",
        "email": "student@scarletmail.rutgers.edu",
        "email_verified": False,
        "firebase": {"sign_in_provider": "password"},
    }
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded):
        with pytest.raises(HTTPException) as exc:
            verify_firebase_token("valid-token")
    assert exc.value.status_code == 403


def test_verify_firebase_token_invalid_token_raises_401():
    with patch(
        "app.services.auth_service.firebase_auth.verify_id_token",
        side_effect=Exception("token expired"),
    ):
        with pytest.raises(HTTPException) as exc:
            verify_firebase_token("bad-token")
    assert exc.value.status_code == 401


# ── get_or_create_user ─────────────────────────────────────────────────────────

async def test_get_or_create_user_creates_new():
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    firebase_data = {"uid": "new-uid", "email": "new@rutgers.edu", "name": "New User"}
    user = await get_or_create_user(db, firebase_data)

    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    assert user.google_uid == "new-uid"
    assert user.email == "new@rutgers.edu"
    assert user.role is None
    assert user.is_verified is None


async def test_get_or_create_user_returns_existing():
    existing = User(google_uid="existing-uid", email="existing@rutgers.edu")
    db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=mock_result)

    firebase_data = {"uid": "existing-uid", "email": "existing@rutgers.edu", "name": "X"}
    user = await get_or_create_user(db, firebase_data)

    db.add.assert_not_called()
    assert user is existing


# ── create_access_token ────────────────────────────────────────────────────────

def test_create_access_token_payload(rsa_keys):
    private_pem, public_pem = rsa_keys
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = UserRole.student

    with patch("app.services.auth_service.settings") as mock_cfg:
        mock_cfg.jwt_private_key = private_pem
        token = create_access_token(user)

    payload = _decode(token, public_pem)
    assert payload["sub"] == str(user.id)
    assert payload["email"] == "test@rutgers.edu"
    assert payload["role"] == "student"
    assert "iat" in payload


def test_create_access_token_null_role(rsa_keys):
    private_pem, public_pem = rsa_keys
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = None

    with patch("app.services.auth_service.settings") as mock_cfg:
        mock_cfg.jwt_private_key = private_pem
        token = create_access_token(user)

    payload = _decode(token, public_pem)
    assert payload["role"] is None


# ── create_refresh_token ───────────────────────────────────────────────────────

async def test_create_refresh_token_stores_in_redis():
    redis_mock = AsyncMock()
    user_id = uuid.uuid4()

    raw_token = await create_refresh_token(user_id, redis_mock)

    assert len(raw_token) == 64  # secrets.token_hex(32) = 64 hex chars
    expected_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    redis_mock.setex.assert_awaited_once_with(
        f"refresh:{expected_hash}",
        7 * 24 * 60 * 60,
        str(user_id),
    )


# ── verify_and_rotate_refresh_token ───────────────────────────────────────────

async def test_verify_and_rotate_success(rsa_keys):
    private_pem, public_pem = rsa_keys
    user_id = uuid.uuid4()
    raw_token = "a" * 64
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    existing_user = User()
    existing_user.id = user_id
    existing_user.email = "test@rutgers.edu"
    existing_user.role = UserRole.student

    redis_mock = AsyncMock()
    redis_mock.getdel = AsyncMock(return_value=str(user_id))

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_user
    db.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.auth_service.settings") as mock_cfg:
        mock_cfg.jwt_private_key = private_pem
        access_token, new_refresh = await verify_and_rotate_refresh_token(raw_token, redis_mock, db)

    payload = _decode(access_token, public_pem)
    assert payload["sub"] == str(user_id)
    assert len(new_refresh) == 64


async def test_verify_and_rotate_invalid_token_raises_401():
    redis_mock = AsyncMock()
    redis_mock.getdel = AsyncMock(return_value=None)
    redis_mock.get = AsyncMock(return_value=None)  # not a previously-consumed token
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await verify_and_rotate_refresh_token("bad-token", redis_mock, db)
    assert exc.value.status_code == 401


# ── Refresh-token family reuse detection (finding 3) ──────────────────────────


async def test_reuse_of_rotated_token_revokes_family():
    """Replaying an already-rotated (consumed) refresh token revokes the whole
    family via revoke_all_refresh_tokens and returns 401."""
    user_id = uuid.uuid4()
    raw_token = "c" * 64
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    redis_mock = AsyncMock()
    # getdel misses (token already consumed), but the consumed-marker maps it to a user.
    redis_mock.getdel = AsyncMock(return_value=None)

    async def _get(key):
        return str(user_id) if key == f"refresh_consumed:{token_hash}" else None

    redis_mock.get = AsyncMock(side_effect=_get)
    redis_mock.smembers = AsyncMock(return_value={"hA", "hB"})
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await verify_and_rotate_refresh_token(raw_token, redis_mock, db)
    assert exc.value.status_code == 401
    assert "reuse" in exc.value.detail.lower()
    # Entire family revoked: index read + deletes issued.
    redis_mock.smembers.assert_awaited_once_with(f"refresh_user:{user_id}")
    assert redis_mock.delete.await_count >= 1


async def test_rotate_records_consumed_marker():
    """A successful rotation records the consumed token hash so a later replay is
    detectable."""
    user_id = uuid.uuid4()
    raw_token = "d" * 64
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    user = User()
    user.id = user_id
    user.email = "a@rutgers.edu"
    user.role = UserRole.student

    redis_mock = AsyncMock()
    redis_mock.getdel = AsyncMock(return_value=str(user_id))
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)

    from app import config as app_config
    orig_priv = app_config.settings.jwt_private_key
    app_config.settings.jwt_private_key = orig_priv or _TEST_PRIV
    try:
        await verify_and_rotate_refresh_token(raw_token, redis_mock, db)
    finally:
        app_config.settings.jwt_private_key = orig_priv

    consumed_calls = [
        c for c in redis_mock.setex.await_args_list
        if c.args and c.args[0] == f"refresh_consumed:{token_hash}"
    ]
    assert consumed_calls, "expected a refresh_consumed marker to be written"


# ── Access-token denylist helpers (finding 2) ─────────────────────────────────


async def test_add_and_check_denylist():
    redis_mock = AsyncMock()
    now = int(datetime.now(timezone.utc).timestamp())
    await add_access_token_to_denylist({"jti": "j1", "exp": now + 300}, redis_mock)
    redis_mock.setex.assert_awaited_once()
    args = redis_mock.setex.await_args.args
    assert args[0] == "denylist:j1"
    assert 0 < args[1] <= 300

    redis_mock.exists = AsyncMock(return_value=1)
    assert await is_access_token_denylisted("j1", redis_mock) is True
    redis_mock.exists = AsyncMock(return_value=0)
    assert await is_access_token_denylisted("j1", redis_mock) is False


async def test_add_denylist_skips_expired_token():
    redis_mock = AsyncMock()
    now = int(datetime.now(timezone.utc).timestamp())
    await add_access_token_to_denylist({"jti": "old", "exp": now - 10}, redis_mock)
    redis_mock.setex.assert_not_awaited()


# ── Non-self-service professor verification (finding 1) ───────────────────────


async def test_mark_professor_verified_sets_flag():
    user_id = uuid.uuid4()
    user = User()
    user.id = user_id
    user.email = "p@rutgers.edu"
    user.role = UserRole.professor
    user.is_verified = False

    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    out = await mark_professor_verified(db, user_id)
    assert out is user
    assert user.is_verified is True


async def test_mark_professor_verified_ignores_non_professor():
    user = User()
    user.id = uuid.uuid4()
    user.role = UserRole.student
    user.is_verified = True

    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)

    assert await mark_professor_verified(db, user.id) is None


async def test_verify_and_rotate_user_deleted_raises_401():
    user_id = uuid.uuid4()
    raw_token = "b" * 64

    redis_mock = AsyncMock()
    redis_mock.getdel = AsyncMock(return_value=str(user_id))

    db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # user was deleted
    db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc:
        await verify_and_rotate_refresh_token(raw_token, redis_mock, db)
    assert exc.value.status_code == 401


def test_create_access_token_includes_jti(rsa_keys):
    from app import config as app_config
    private_pem, public_pem = rsa_keys
    orig_priv, orig_pub = app_config.settings.jwt_private_key, app_config.settings.jwt_public_key
    app_config.settings.jwt_private_key = private_pem
    app_config.settings.jwt_public_key = public_pem
    try:
        user = User()
        user.id = uuid.uuid4()
        user.email = "a@rutgers.edu"
        user.role = UserRole.student
        token = create_access_token(user)
        payload = _decode(token, public_pem)
        assert "jti" in payload
        uuid.UUID(payload["jti"])  # parses as a uuid
    finally:
        app_config.settings.jwt_private_key = orig_priv
        app_config.settings.jwt_public_key = orig_pub


# ── Per-user refresh-token index ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_refresh_token_adds_to_user_index():
    redis = AsyncMock()
    user_id = uuid.uuid4()
    raw = await create_refresh_token(user_id, redis)
    expected_hash = hashlib.sha256(raw.encode()).hexdigest()
    redis.setex.assert_awaited_once()
    redis.sadd.assert_awaited_once_with(f"refresh_user:{user_id}", expected_hash)
    redis.expire.assert_awaited_once()  # TTL set on the index set


@pytest.mark.asyncio
async def test_rotate_removes_old_hash_from_user_index():
    user_id = uuid.uuid4()
    old_raw = "old-token"
    old_hash = hashlib.sha256(old_raw.encode()).hexdigest()
    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=str(user_id))

    db = MagicMock()
    user = User()
    user.id = user_id
    user.email = "a@rutgers.edu"
    user.role = UserRole.student
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)

    from app import config as app_config
    orig_priv = app_config.settings.jwt_private_key
    app_config.settings.jwt_private_key = orig_priv or _TEST_PRIV
    try:
        await verify_and_rotate_refresh_token(old_raw, redis, db)
        redis.srem.assert_awaited_once_with(f"refresh_user:{user_id}", old_hash)
    finally:
        app_config.settings.jwt_private_key = orig_priv
