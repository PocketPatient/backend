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

    # Counting Redis mock — uses a pipeline to match the middleware's atomic INCR+EXPIRE
    _counts: dict[str, int] = {}

    class FakePipeline:
        def __init__(self):
            self._cmds = []

        def incr(self, key: str):
            self._cmds.append(("incr", key))
            return self

        def expire(self, key: str, seconds: int):
            self._cmds.append(("expire", key, seconds))
            return self

        async def execute(self):
            results = []
            for cmd in self._cmds:
                if cmd[0] == "incr":
                    k = cmd[1]
                    _counts[k] = _counts.get(k, 0) + 1
                    results.append(_counts[k])
                elif cmd[0] == "expire":
                    results.append(True)
            return results

    redis_mock = AsyncMock()
    redis_mock.pipeline = lambda: FakePipeline()
    app.state.redis = redis_mock

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.state.redis = None  # reset so next test's fixture sets it clean
    app_config.settings.jwt_public_key = original_key
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_error_responses_include_code(client):
    """All error responses must include a 'code' field."""
    resp = await client.get(
        "/api/v1/courses/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": "Bearer bad"},
    )
    # bad JWT → 401
    assert resp.status_code == 401
    data = resp.json()
    assert "code" in data
    assert data["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_validation_error_includes_code(client):
    """Pydantic validation errors (422) must include a 'code' field."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"wrong_field": "value"},
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "code" in data
    assert data["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_rate_limit_auth_endpoint(client_with_counting_redis):
    """Auth endpoint: 10 req/min limit. 11th request gets 429."""
    client = client_with_counting_redis
    for i in range(10):
        resp = await client.post("/api/v1/auth/login", json={"firebase_id_token": "bad"})
        assert resp.status_code != 429, f"Got 429 on request {i + 1}"
    resp = await client.post("/api/v1/auth/login", json={"firebase_id_token": "bad"})
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_rate_limit_message_endpoint(client_with_counting_redis):
    """Message send: 30 req/min. 31st POST gets 429 (routing errors still count)."""
    client = client_with_counting_redis
    path = f"/api/v1/sessions/{_uuid.uuid4()}/messages"
    for i in range(30):
        resp = await client.post(path, json={"content": "hi"})
        assert resp.status_code != 429, f"Got 429 on request {i + 1}"
    resp = await client.post(path, json={"content": "hi"})
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_rate_limit_analytics_endpoint(client_with_counting_redis):
    """Analytics: 60 req/min. 61st request gets 429."""
    client = client_with_counting_redis
    path = "/api/v1/analytics/overview"
    for i in range(60):
        resp = await client.get(path)
        assert resp.status_code != 429, f"Got 429 on request {i + 1}"
    resp = await client.get(path)
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_standard_endpoint(client_with_counting_redis):
    """Other endpoints: 100 req/min. 101st request gets 429."""
    client = client_with_counting_redis
    path = f"/api/v1/courses/{_uuid.uuid4()}"
    for i in range(100):
        resp = await client.get(path)
        assert resp.status_code != 429, f"Got 429 on request {i + 1}"
    resp = await client.get(path)
    assert resp.status_code == 429
