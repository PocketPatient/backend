import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.models.user import User, UserRole


def make_user():
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = None
    user.is_verified = None
    user.display_name = "Test User"
    return user


@pytest.fixture
def client():
    app.state.redis = AsyncMock()
    return TestClient(app)


def test_login_returns_tokens(client):
    user = make_user()
    with patch("app.routers.auth.auth_service") as svc:
        svc.verify_firebase_token.return_value = {
            "uid": "uid1", "email": "test@rutgers.edu",
            "name": "Test", "sign_in_provider": "google.com",
        }
        svc.get_or_create_user = AsyncMock(return_value=user)
        svc.create_access_token.return_value = "acc-token"
        svc.create_refresh_token = AsyncMock(return_value="ref-token")

        response = client.post("/api/v1/auth/login", json={"firebase_id_token": "firebase-tok"})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "acc-token"
    assert body["refresh_token"] == "ref-token"
    assert body["token_type"] == "bearer"


def test_login_non_rutgers_returns_403(client):
    with patch("app.routers.auth.auth_service") as svc:
        svc.verify_firebase_token.side_effect = HTTPException(
            status_code=403, detail="Must use a Rutgers email address"
        )
        response = client.post("/api/v1/auth/login", json={"firebase_id_token": "bad-tok"})
    assert response.status_code == 403


def test_login_missing_body_returns_422(client):
    response = client.post("/api/v1/auth/login", json={})
    assert response.status_code == 422


def test_refresh_returns_new_tokens(client):
    with patch("app.routers.auth.auth_service") as svc:
        svc.verify_and_rotate_refresh_token = AsyncMock(
            return_value=("new-acc", "new-ref")
        )
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "old-ref"})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "new-acc"
    assert body["refresh_token"] == "new-ref"


def test_refresh_expired_token_returns_401(client):
    with patch("app.routers.auth.auth_service") as svc:
        svc.verify_and_rotate_refresh_token = AsyncMock(
            side_effect=HTTPException(status_code=401, detail="Refresh token invalid or expired")
        )
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "expired"})
    assert response.status_code == 401


def test_logout_revokes_all_refresh_tokens(client):
    from app import config as app_config
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from jose import jwt as _jwt

    priv = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    user = make_user()
    user.role = UserRole.student
    token = _jwt.encode({"sub": str(user.id)}, priv_pem, algorithm="RS256")

    redis = AsyncMock()
    redis.smembers = AsyncMock(return_value={"h1", "h2"})
    app.state.redis = redis

    orig_pub = app_config.settings.jwt_public_key
    app_config.settings.jwt_public_key = pub_pem
    # Override the auth dependency directly so no DB user row is required.
    from app.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    finally:
        app_config.settings.jwt_public_key = orig_pub
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 204
    redis.smembers.assert_awaited_once_with(f"refresh_user:{user.id}")
    delete_targets = {c.args[0] for c in redis.delete.await_args_list}
    assert "refresh:h1" in delete_targets
    assert "refresh:h2" in delete_targets
    assert f"refresh_user:{user.id}" in delete_targets


def test_logout_requires_auth(client):
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 401
