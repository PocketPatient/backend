from __future__ import annotations

import json
import uuid
from typing import Any

SUMMARY_TTL_SEC = 300


def summary_key(user_id: uuid.UUID, course_id: uuid.UUID) -> str:
    return f"analytics:summary:{user_id}:{course_id}"


def class_summary_key(course_id: uuid.UUID) -> str:
    return f"analytics:class:{course_id}"


async def get_cached_json(redis: Any, key: str) -> dict | None:
    if redis is None:
        return None
    try:
        cached = await redis.get(key)
    except Exception:
        return None
    if isinstance(cached, str):
        try:
            return json.loads(cached)
        except (ValueError, TypeError):
            return None
    return None


async def set_cached_json(redis: Any, key: str, value: dict, ttl: int = SUMMARY_TTL_SEC) -> None:
    if redis is None:
        return
    try:
        await redis.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:
        pass


async def invalidate(redis: Any, key: str) -> None:
    if redis is None:
        return
    try:
        await redis.delete(key)
    except Exception:
        pass
