import uuid as _uuid

import pytest


@pytest.mark.asyncio
async def test_logging_middleware_adds_request_id(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers
    # Raises ValueError if header is not a valid UUID
    _uuid.UUID(resp.headers["x-request-id"])


@pytest.fixture
async def client_with_counting_redis(rsa_keys, test_db):
    """Client where Redis.incr() actually counts per key."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.database import get_db
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from unittest.mock import AsyncMock
    import os

    TEST_DATABASE_URL = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient_test",
    )
    from app import config as app_config

    private_pem, public_pem = rsa_keys
    original_key = app_config.settings.jwt_public_key
    app_config.settings.jwt_public_key = public_pem

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Counting Redis mock
    _counts: dict[str, int] = {}

    async def fake_incr(key: str) -> int:
        _counts[key] = _counts.get(key, 0) + 1
        return _counts[key]

    async def fake_expire(key: str, seconds: int) -> None:
        pass

    redis_mock = AsyncMock()
    redis_mock.incr = fake_incr
    redis_mock.expire = fake_expire
    app.state.redis = redis_mock

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app_config.settings.jwt_public_key = original_key
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_rate_limit_auth_endpoint(client_with_counting_redis):
    """Auth endpoint: 20 req/min limit. 21st request gets 429."""
    client = client_with_counting_redis
    for i in range(20):
        resp = await client.post(
            "/api/v1/auth/login",
            json={"firebase_id_token": "bad"},
        )
        assert resp.status_code != 429, f"Got 429 on request {i + 1}"

    resp = await client.post(
        "/api/v1/auth/login",
        json={"firebase_id_token": "bad"},
    )
    assert resp.status_code == 429
    data = resp.json()
    assert data["code"] == "RATE_LIMIT_EXCEEDED"
