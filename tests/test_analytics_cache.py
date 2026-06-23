from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from app.services.analytics_cache import (
    get_cached_json,
    invalidate,
    set_cached_json,
    summary_key,
)


def test_summary_key_format():
    uid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    cid = uuid.UUID("22222222-2222-2222-2222-222222222222")
    assert summary_key(uid, cid) == f"analytics:summary:{uid}:{cid}"


async def test_get_cached_json_returns_none_for_non_string():
    # AsyncMock().get(...) resolves to a MagicMock, not a str -> cache miss.
    redis = AsyncMock()
    assert await get_cached_json(redis, "k") is None


async def test_get_cached_json_parses_string_hit():
    redis = AsyncMock()
    redis.get.return_value = '{"a": 1}'
    assert await get_cached_json(redis, "k") == {"a": 1}


async def test_get_cached_json_none_redis():
    assert await get_cached_json(None, "k") is None


async def test_set_cached_json_writes_with_ttl():
    redis = AsyncMock()
    await set_cached_json(redis, "k", {"a": 1}, ttl=300)
    redis.set.assert_awaited_once()
    args, kwargs = redis.set.call_args
    assert args[0] == "k"
    assert kwargs.get("ex") == 300


async def test_set_cached_json_none_redis_noop():
    await set_cached_json(None, "k", {"a": 1})  # must not raise


async def test_invalidate_deletes_key():
    redis = AsyncMock()
    await invalidate(redis, "k")
    redis.delete.assert_awaited_once_with("k")


async def test_invalidate_swallows_errors():
    redis = AsyncMock()
    redis.delete.side_effect = RuntimeError("boom")
    await invalidate(redis, "k")  # must not raise
