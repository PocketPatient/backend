import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from app.deps import get_current_user, require_role
from app.models.user import User, UserRole


def make_user(role=None):
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = role
    user.is_verified = None
    user.display_name = "Test"
    return user


async def test_get_current_user_valid_token(rsa_keys):
    private_pem, public_pem = rsa_keys
    user = make_user(UserRole.student)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": "student",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    token = jwt.encode(payload, private_pem, algorithm="RS256")

    db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=mock_result)

    with patch("app.deps.settings") as mock_cfg:
        mock_cfg.jwt_public_key = public_pem
        result = await get_current_user(authorization=f"Bearer {token}", db=db)

    assert result is user


async def test_get_current_user_missing_header_raises_401():
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization=None, db=db)
    assert exc.value.status_code == 401


async def test_get_current_user_no_bearer_prefix_raises_401():
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization="token-without-bearer", db=db)
    assert exc.value.status_code == 401


async def test_get_current_user_invalid_token_raises_401():
    db = MagicMock()
    with patch("app.deps.settings") as mock_cfg:
        mock_cfg.jwt_public_key = "not-a-real-key"
        with pytest.raises(HTTPException) as exc:
            await get_current_user(authorization="Bearer bad.token.here", db=db)
    assert exc.value.status_code == 401


async def test_require_role_matching_role_passes():
    user = make_user(UserRole.professor)
    dependency_fn = require_role("professor")
    result = await dependency_fn(current_user=user)
    assert result is user


async def test_require_role_wrong_role_raises_403():
    user = make_user(UserRole.student)
    dependency_fn = require_role("professor")
    with pytest.raises(HTTPException) as exc:
        await dependency_fn(current_user=user)
    assert exc.value.status_code == 403


async def test_require_role_no_role_raises_403():
    user = make_user(role=None)
    dependency_fn = require_role("student")
    with pytest.raises(HTTPException) as exc:
        await dependency_fn(current_user=user)
    assert exc.value.status_code == 403
