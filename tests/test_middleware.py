import uuid as _uuid

import pytest


@pytest.mark.asyncio
async def test_logging_middleware_adds_request_id(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "x-request-id" in resp.headers
    # Raises ValueError if header is not a valid UUID
    _uuid.UUID(resp.headers["x-request-id"])
