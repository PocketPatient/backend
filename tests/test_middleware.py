import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_logging_middleware_adds_request_id(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers
    # UUID format: 8-4-4-4-12
    parts = resp.headers["x-request-id"].split("-")
    assert len(parts) == 5
