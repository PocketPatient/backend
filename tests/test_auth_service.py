import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from app.models.user import User, UserRole
from app.services.auth_service import (
    create_access_token,
    create_refresh_token,
    get_or_create_user,
    verify_and_rotate_refresh_token,
    verify_firebase_token,
)


# ── verify_firebase_token ──────────────────────────────────────────────────────

def test_verify_firebase_token_scarletmail():
    decoded = {
        "uid": "uid1",
        "email": "student@scarletmail.rutgers.edu",
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
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)

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
    db = AsyncMock()
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

    payload = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert payload["sub"] == str(user.id)
    assert payload["email"] == "test@rutgers.edu"
    assert payload["role"] == "student"


def test_create_access_token_null_role(rsa_keys):
    private_pem, public_pem = rsa_keys
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = None

    with patch("app.services.auth_service.settings") as mock_cfg:
        mock_cfg.jwt_private_key = private_pem
        token = create_access_token(user)

    payload = jwt.decode(token, public_pem, algorithms=["RS256"])
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
    redis_mock.get = AsyncMock(return_value=str(user_id))

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_user
    db.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.auth_service.settings") as mock_cfg:
        mock_cfg.jwt_private_key = private_pem
        access_token, new_refresh = await verify_and_rotate_refresh_token(raw_token, redis_mock, db)

    redis_mock.delete.assert_awaited_once_with(f"refresh:{token_hash}")
    payload = jwt.decode(access_token, public_pem, algorithms=["RS256"])
    assert payload["sub"] == str(user_id)
    assert len(new_refresh) == 64


async def test_verify_and_rotate_invalid_token_raises_401():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await verify_and_rotate_refresh_token("bad-token", redis_mock, db)
    assert exc.value.status_code == 401
