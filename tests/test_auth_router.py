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
