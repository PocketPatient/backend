from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_AUTH_PREFIX = "/api/v1/auth"
_ANALYTICS_PREFIX = "/api/v1/analytics"
_MESSAGE_RE = re.compile(r"^/api/v1/sessions/[^/]+/messages/?$")

_AUTH_LIMIT = 10
_MESSAGE_LIMIT = 30
_ANALYTICS_LIMIT = 60
_STANDARD_LIMIT = 100
_WINDOW_SECONDS = 60

# Number of trusted reverse proxies sitting in front of this app. Only the
# X-Forwarded-For entries contributed by our own proxies may be trusted; the
# client controls everything to the left of them. Default 0 = never trust XFF
# (fail closed) so an attacker cannot forge the rate-limit key.
_TRUSTED_PROXY_COUNT = 0


def _socket_ip(request: Request) -> str:
    """The real TCP peer address — cannot be spoofed by request headers."""
    return request.client.host if request.client else "unknown"


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate-keying.

    X-Forwarded-For is honored only for the number of proxies we actually
    operate (`_TRUSTED_PROXY_COUNT`); with the default of 0 the header is
    ignored entirely and the socket peer is used. This prevents a client from
    minting unlimited rate-limit buckets by rotating the XFF header.
    """
    if _TRUSTED_PROXY_COUNT > 0:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            # The rightmost _TRUSTED_PROXY_COUNT entries were appended by our
            # own proxies; the entry immediately to their left is the real
            # client. Anything further left is attacker-controlled.
            idx = len(parts) - _TRUSTED_PROXY_COUNT - 1
            if 0 <= idx < len(parts):
                return parts[idx]
    return _socket_ip(request)


def _extract_user_id(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.removeprefix("Bearer ")
    try:
        # Signature integrity is irrelevant for *rate-keying* — authentication
        # is still fully enforced downstream by get_current_user. Reading the
        # unverified claims avoids a redundant RS256 verification per request.
        return jwt.get_unverified_claims(token).get("sub")
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
            # Security-sensitive brute-force bucket: key on the real socket
            # peer only, never the client-supplied X-Forwarded-For header.
            key = f"rl:auth:{_socket_ip(request)}"
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
            count = (await pipe.execute())[0]
            # Set the TTL only when the counter is first created, so each fixed
            # 60s window actually expires. Refreshing it every request would let
            # the count accumulate forever under steady traffic and permanently
            # 429 a compliant client.
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
