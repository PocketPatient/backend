from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

_AUTH_PREFIX = "/api/v1/auth"
_ANALYTICS_PREFIX = "/api/v1/analytics"
_MESSAGE_RE = re.compile(r"^/api/v1/sessions/[^/]+/messages/?$")

_AUTH_LIMIT = 10
_MESSAGE_LIMIT = 30
_ANALYTICS_LIMIT = 60
_STANDARD_LIMIT = 100
_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _extract_user_id(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.jwt_public_key, algorithms=["RS256"])
        return payload.get("sub")
    except Exception:
        return None


def _user_key(request: Request, tier: str) -> str:
    user_id = _extract_user_id(request)
    if user_id:
        return f"rl:{tier}:user:{user_id}"
    return f"rl:{tier}:ip:{_client_ip(request)}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        redis = getattr(getattr(request, "app", None), "state", None)
        redis = getattr(redis, "redis", None) if redis else None
        if redis is None:
            return await call_next(request)

        path = request.url.path
        if path.startswith(_AUTH_PREFIX):
            limit = _AUTH_LIMIT
            key = f"rl:auth:{_client_ip(request)}"
        elif request.method == "POST" and _MESSAGE_RE.match(path):
            limit = _MESSAGE_LIMIT
            key = _user_key(request, "msg")
        elif path.startswith(_ANALYTICS_PREFIX):
            limit = _ANALYTICS_LIMIT
            key = _user_key(request, "analytics")
        else:
            limit = _STANDARD_LIMIT
            key = _user_key(request, "std")

        try:
            pipe = redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, _WINDOW_SECONDS)
            count, _ = await pipe.execute()
            if count > limit:
                return JSONResponse(
                    {"detail": "Rate limit exceeded", "code": "RATE_LIMIT_EXCEEDED"},
                    status_code=429,
                )
        except Exception:
            pass  # fail open if Redis is unavailable

        return await call_next(request)
