import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import app
from app.models.user import User, UserRole


def make_user(role=None, is_verified=None):
    user = User()
    user.id = uuid.uuid4()
    user.google_uid = "test-uid"
    user.email = "test@rutgers.edu"
    user.role = role
    user.is_verified = is_verified
    user.display_name = "Test User"
    user.created_at = datetime.now(timezone.utc)
    return user


@pytest.fixture
def authed_client():
    """TestClient with get_current_user overridden to return a student user."""
    user = make_user(UserRole.student, is_verified=True)

    async def _override_user():
        return user

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user
    yield TestClient(app), user
    app.dependency_overrides.clear()


# ── GET /users/me ──────────────────────────────────────────────────────────────

def test_get_me_returns_user_profile(authed_client):
    client, user = authed_client
    response = client.get("/api/v1/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "test@rutgers.edu"
    assert body["role"] == "student"
    assert body["is_verified"] is True


def test_get_me_no_auth_returns_401():
    app.state.redis = AsyncMock()
    client = TestClient(app)
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


# ── PUT /users/me/role ─────────────────────────────────────────────────────────

def test_set_role_student_succeeds():
    user = make_user(role=None, is_verified=None)

    async def _override_user():
        return user

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    async def _override_db():
        yield mock_db

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db

    client = TestClient(app)
    response = client.put("/api/v1/users/me/role", json={"role": "student"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert user.role == UserRole.student
    assert user.is_verified is True


def test_set_role_professor_sets_is_verified_false():
    user = make_user(role=None, is_verified=None)

    async def _override_user():
        return user

    mock_db = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()

    async def _override_db():
        yield mock_db

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db

    client = TestClient(app)
    response = client.put("/api/v1/users/me/role", json={"role": "professor"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert user.role == UserRole.professor
    assert user.is_verified is False


def test_set_role_already_set_returns_409():
    user = make_user(role=UserRole.student, is_verified=True)

    async def _override_user():
        return user

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user

    client = TestClient(app)
    response = client.put("/api/v1/users/me/role", json={"role": "professor"})

    app.dependency_overrides.clear()

    assert response.status_code == 409


def test_set_role_invalid_value_returns_422():
    user = make_user(role=None)

    async def _override_user():
        return user

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user

    client = TestClient(app)
    response = client.put("/api/v1/users/me/role", json={"role": "admin"})

    app.dependency_overrides.clear()

    assert response.status_code == 422
