import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from app.deps import get_current_user, require_role
from app.models.user import User, UserRole
from app.services.auth_service import JWT_AUDIENCE, JWT_ISSUER


def make_user(role=None, is_verified=None):
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = role
    user.is_verified = is_verified
    user.display_name = "Test"
    return user


def _mint(user_id, private_pem, *, iss=JWT_ISSUER, aud=JWT_AUDIENCE, jti="jti-1"):
    payload = {
        "sub": str(user_id),
        "email": "test@rutgers.edu",
        "role": "student",
        "jti": jti,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    if iss is not None:
        payload["iss"] = iss
    if aud is not None:
        payload["aud"] = aud
    return jwt.encode(payload, private_pem, algorithm="RS256")


async def test_get_current_user_valid_token(rsa_keys):
    private_pem, public_pem = rsa_keys
    user = make_user(UserRole.student)
    token = _mint(user.id, private_pem)

    db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=mock_result)

    with patch("app.deps.settings") as mock_cfg:
        mock_cfg.jwt_public_key = public_pem
        result = await get_current_user(authorization=f"Bearer {token}", db=db)

    assert result is user


async def test_get_current_user_missing_issuer_audience_rejected(rsa_keys):
    """A token minted without iss/aud must not authenticate (finding 4)."""
    private_pem, public_pem = rsa_keys
    user = make_user(UserRole.student)
    token = _mint(user.id, private_pem, iss=None, aud=None)

    db = MagicMock()
    with patch("app.deps.settings") as mock_cfg:
        mock_cfg.jwt_public_key = public_pem
        with pytest.raises(HTTPException) as exc:
            await get_current_user(authorization=f"Bearer {token}", db=db)
    assert exc.value.status_code == 401


async def test_get_current_user_denylisted_jti_rejected(rsa_keys):
    """A logged-out (denylisted) access token is rejected within its TTL (finding 2)."""
    private_pem, public_pem = rsa_keys
    user = make_user(UserRole.student)
    token = _mint(user.id, private_pem, jti="revoked-jti")

    db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=mock_result)

    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=1)  # jti is on the denylist
    request = MagicMock()
    request.app.state.redis = redis

    with patch("app.deps.settings") as mock_cfg:
        mock_cfg.jwt_public_key = public_pem
        with pytest.raises(HTTPException) as exc:
            await get_current_user(request=request, authorization=f"Bearer {token}", db=db)
    assert exc.value.status_code == 401
    redis.exists.assert_awaited_once_with("denylist:revoked-jti")


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
    user = make_user(UserRole.professor, is_verified=True)
    dependency_fn = require_role("professor")
    result = await dependency_fn(current_user=user)
    assert result is user


async def test_require_role_unverified_professor_blocked():
    """A self-assigned professor (is_verified=False) is blocked from professor-only
    surfaces even though their role is 'professor' (finding 1)."""
    user = make_user(UserRole.professor, is_verified=False)
    dependency_fn = require_role("professor")
    with pytest.raises(HTTPException) as exc:
        await dependency_fn(current_user=user)
    assert exc.value.status_code == 403


async def test_require_role_verified_student_unaffected():
    """The verification gate applies only to the professor role."""
    user = make_user(UserRole.student, is_verified=True)
    dependency_fn = require_role("student")
    assert await dependency_fn(current_user=user) is user


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
