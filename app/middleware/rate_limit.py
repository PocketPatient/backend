from __future__ import annotations

from collections.abc import Awaitable, Callable

from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

_AUTH_PREFIX = "/api/v1/auth"
_AUTH_LIMIT = 20
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
    except (JWTError, Exception):
        return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        redis = getattr(getattr(request, "app", None), "state", None)
        redis = getattr(redis, "redis", None) if redis else None
        if redis is None:
            return await call_next(request)

        is_auth = request.url.path.startswith(_AUTH_PREFIX)
        limit = _AUTH_LIMIT if is_auth else _STANDARD_LIMIT

        if is_auth:
            key = f"rl:auth:{_client_ip(request)}"
        else:
            user_id = _extract_user_id(request)
            key = f"rl:user:{user_id}" if user_id else f"rl:ip:{_client_ip(request)}"

        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, _WINDOW_SECONDS)
            if count > limit:
                return JSONResponse(
                    {"detail": "Rate limit exceeded", "code": "RATE_LIMIT_EXCEEDED"},
                    status_code=429,
                )
        except Exception:
            pass  # fail open if Redis is unavailable

        return await call_next(request)
