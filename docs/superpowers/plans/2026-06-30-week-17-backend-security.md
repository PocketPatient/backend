# Week 17 Backend Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the PocketPatient backend with input sanitization, tiered rate limiting, FERPA-compliance verification + docs, and JWT hardening (jti claim, per-user refresh-token index, logout revocation).

**Architecture:** Add a stdlib-only sanitization helper wired into Pydantic request schemas; extend the existing `RateLimitMiddleware` from 2 to 4 tiers; add a per-user Redis index over the already-hashed refresh tokens plus a `/auth/logout` endpoint; prove RBAC isolation with cross-tenant tests and document the compliance posture.

**Tech Stack:** FastAPI 0.111, SQLAlchemy 2.0 async, Pydantic v2, Redis (aioredis), pytest + pytest-asyncio, python-jose (RS256).

## Global Constraints

- Python 3.11+; all commands via `uv` (`uv run pytest ...`).
- **No new third-party dependencies** — sanitization is stdlib-only.
- Ownership/existence checks return **404** (never 403) to avoid leaking existence.
- Error responses use the `{detail, code}` envelope; validation failures surface as **422** with `code: "VALIDATION_ERROR"`.
- Rate limiting **fails open** when Redis is unavailable.
- Refresh tokens are stored as **SHA-256 hashes in Redis** only (never the raw token); single-use with rotation.
- TDD: write failing test → run to confirm failure → minimal impl → run to confirm pass → commit.
- Auth is `Authorization: Bearer` — no cookies (secure-cookie item is N/A).

---

### Task 1: Sanitization service

**Files:**
- Create: `app/services/sanitize.py`
- Test: `tests/test_sanitize.py`

**Interfaces:**
- Produces: `strip_tags(value: str) -> str` (removes HTML tags, collapses whitespace, trims); `sanitize_text(value: str, max_len: int) -> str` (strip_tags then raises `ValueError` if the cleaned length exceeds `max_len`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sanitize.py
import pytest

from app.services.sanitize import sanitize_text, strip_tags


def test_strip_tags_removes_html():
    assert strip_tags("<b>hello</b>") == "hello"


def test_strip_tags_removes_script_content_markup():
    # Tags are removed; inner text is kept (Flutter frontend never renders it as HTML).
    assert strip_tags("<script>alert(1)</script>hi") == "alert(1)hi"


def test_strip_tags_collapses_whitespace_and_trims():
    assert strip_tags("  a\n\n b\t c  ") == "a b c"


def test_strip_tags_plain_text_unchanged():
    assert strip_tags("Hello, how are you?") == "Hello, how are you?"


def test_sanitize_text_returns_cleaned_within_limit():
    assert sanitize_text("<i>ok</i>", 10) == "ok"


def test_sanitize_text_raises_when_cleaned_exceeds_limit():
    with pytest.raises(ValueError):
        sanitize_text("x" * 11, 10)


def test_sanitize_text_limit_applies_to_cleaned_not_raw():
    # Raw is 20 chars but cleaned "ok" is 2 — must pass under limit 5.
    assert sanitize_text("<span>ok</span>ok", 5) == "okok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sanitize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.sanitize'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/sanitize.py
from __future__ import annotations

import re
from html.parser import HTMLParser

_WHITESPACE_RE = re.compile(r"\s+")


class _TagStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def strip_tags(value: str) -> str:
    """Remove HTML tags, collapse runs of whitespace, and trim."""
    parser = _TagStripper()
    parser.feed(value)
    parser.close()
    text = parser.get_text()
    return _WHITESPACE_RE.sub(" ", text).strip()


def sanitize_text(value: str, max_len: int) -> str:
    """Strip tags/whitespace, then enforce a maximum length on the cleaned text."""
    cleaned = strip_tags(value)
    if len(cleaned) > max_len:
        raise ValueError(f"text exceeds maximum length of {max_len} characters")
    return cleaned
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sanitize.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/sanitize.py tests/test_sanitize.py
git commit -m "feat: stdlib HTML-stripping sanitization helper"
```

---

### Task 2: Apply sanitization to request schemas

**Files:**
- Modify: `app/schemas/session.py` (`MessageCreate`, `DiagnosisCreate`)
- Modify: `app/schemas/course.py` (`CourseCreate.title`)
- Test: `tests/test_sanitize.py` (append schema-validator tests)

**Interfaces:**
- Consumes: `sanitize_text` from Task 1.
- Produces: `MessageCreate.content` capped at **2000** cleaned chars; `DiagnosisCreate.primary_dx`/`justification`/`differentials[]` tag-stripped (existing caps 255 / 2000 preserved); `CourseCreate.title` tag-stripped.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sanitize.py  (append)
import pytest
from pydantic import ValidationError

from app.schemas.session import DiagnosisCreate, MessageCreate
from app.schemas.course import CourseCreate


def test_message_create_strips_tags():
    m = MessageCreate(content="<b>Hi doctor</b>")
    assert m.content == "Hi doctor"


def test_message_create_rejects_over_2000_cleaned_chars():
    with pytest.raises(ValidationError):
        MessageCreate(content="x" * 2001)


def test_message_create_rejects_content_emptied_by_stripping():
    with pytest.raises(ValidationError):
        MessageCreate(content="<br><br>")


def test_diagnosis_create_strips_tags_in_fields():
    d = DiagnosisCreate(
        primary_dx="<i>MDD</i>",
        differentials=["<b>GAD</b>"],
        justification="Patient presents with " + "symptoms " * 5,
    )
    assert d.primary_dx == "MDD"
    assert d.differentials == ["GAD"]


def test_course_create_strips_tags_in_title():
    c = CourseCreate(title="<b>Intro to Psychiatry</b>")
    assert c.title == "Intro to Psychiatry"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sanitize.py -v -k "strips or rejects or emptied"`
Expected: FAIL (e.g. `assert '<b>Hi doctor</b>' == 'Hi doctor'`)

- [ ] **Step 3: Write minimal implementation**

In `app/schemas/session.py`, replace the `MessageCreate` class and add a validator to `DiagnosisCreate`. Add the import at the top of the file:

```python
from pydantic import BaseModel, Field, field_validator

from app.services.sanitize import sanitize_text, strip_tags
```

```python
class MessageCreate(BaseModel):
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def _clean_content(cls, v: str) -> str:
        v = sanitize_text(v, 2000)
        if not v:
            raise ValueError("message must not be empty")
        return v
```

```python
class DiagnosisCreate(BaseModel):
    primary_dx: str = Field(min_length=1, max_length=255)
    differentials: list[str] = Field(default_factory=list, max_length=3)
    justification: str = Field(min_length=50, max_length=2000)

    @field_validator("primary_dx", "justification")
    @classmethod
    def _clean_text(cls, v: str) -> str:
        return strip_tags(v)

    @field_validator("differentials")
    @classmethod
    def _clean_differentials(cls, v: list[str]) -> list[str]:
        return [strip_tags(item) for item in v]
```

In `app/schemas/course.py`, update the existing `strip_title` validator on `CourseCreate` to also strip tags (import `strip_tags` at the top):

```python
from app.services.sanitize import strip_tags
```

```python
    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: str) -> str:
        if isinstance(v, str):
            v = strip_tags(v)
        return v
```

Note: `strip_tags` already trims, so it subsumes the previous `.strip()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sanitize.py -v`
Expected: PASS (all)

- [ ] **Step 5: Run the existing schema/router suites to confirm no regressions**

Run: `uv run pytest tests/test_diagnosis_schemas.py tests/test_courses_router.py tests/test_sessions_router.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/schemas/session.py app/schemas/course.py tests/test_sanitize.py
git commit -m "feat: sanitize student and course free-text inputs"
```

---

### Task 3: Tiered rate limiting

**Files:**
- Modify: `app/middleware/rate_limit.py`
- Test: `tests/test_middleware.py`

**Interfaces:**
- Produces: 4 rate-limit tiers keyed in distinct Redis namespaces — auth `10/min` (by IP, `rl:auth:*`), message `30/min` (`POST /api/v1/sessions/{id}/messages`, by user, `rl:msg:*`), analytics `60/min` (`/api/v1/analytics`, by user, `rl:analytics:*`), other `100/min` (by user, `rl:std:*`). Window 60s, fail-open.

- [ ] **Step 1: Write the failing tests**

Update the existing `test_rate_limit_auth_endpoint` in `tests/test_middleware.py` to the new limit of 10, and add message/analytics/standard tier tests. Replace the existing `test_rate_limit_auth_endpoint` body and append the new tests:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_middleware.py -v -k rate_limit`
Expected: FAIL — auth test now expects 429 on the 11th (currently allows 20); message/analytics tests hit the current 100 standard limit so no 429 appears.

- [ ] **Step 3: Write minimal implementation**

Replace the constants block and `dispatch` in `app/middleware/rate_limit.py`. Add `import re` at the top. Keep `_client_ip` and `_extract_user_id` unchanged.

```python
_AUTH_PREFIX = "/api/v1/auth"
_ANALYTICS_PREFIX = "/api/v1/analytics"
_MESSAGE_RE = re.compile(r"^/api/v1/sessions/[^/]+/messages/?$")

_AUTH_LIMIT = 10
_MESSAGE_LIMIT = 30
_ANALYTICS_LIMIT = 60
_STANDARD_LIMIT = 100
_WINDOW_SECONDS = 60


def _user_key(request: Request, tier: str) -> str:
    user_id = _extract_user_id(request)
    if user_id:
        return f"rl:{tier}:user:{user_id}"
    return f"rl:{tier}:ip:{_client_ip(request)}"
```

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_middleware.py -v -k rate_limit`
Expected: PASS (4 tier tests)

- [ ] **Step 5: Commit**

```bash
git add app/middleware/rate_limit.py tests/test_middleware.py
git commit -m "feat: four-tier per-endpoint rate limiting"
```

---

### Task 4: Add `jti` claim to access tokens

**Files:**
- Modify: `app/services/auth_service.py` (`create_access_token`)
- Test: `tests/test_auth_service.py`

**Interfaces:**
- Produces: access tokens now include a `jti` claim (uuid4 string). Signature of `create_access_token(user: User) -> str` unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_service.py  (append)
def test_create_access_token_includes_jti(rsa_keys):
    from app import config as app_config
    private_pem, public_pem = rsa_keys
    orig_priv, orig_pub = app_config.settings.jwt_private_key, app_config.settings.jwt_public_key
    app_config.settings.jwt_private_key = private_pem
    app_config.settings.jwt_public_key = public_pem
    try:
        user = User()
        user.id = uuid.uuid4()
        user.email = "a@rutgers.edu"
        user.role = UserRole.student
        token = create_access_token(user)
        payload = jwt.decode(token, public_pem, algorithms=["RS256"])
        assert "jti" in payload
        uuid.UUID(payload["jti"])  # parses as a uuid
    finally:
        app_config.settings.jwt_private_key = orig_priv
        app_config.settings.jwt_public_key = orig_pub
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_auth_service.py::test_create_access_token_includes_jti -v`
Expected: FAIL with `assert 'jti' in payload`

- [ ] **Step 3: Write minimal implementation**

In `app/services/auth_service.py`, add `"jti": str(uuid.uuid4()),` to the `create_access_token` payload (`uuid` is already imported):

```python
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value if user.role else None,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_auth_service.py::test_create_access_token_includes_jti -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/auth_service.py tests/test_auth_service.py
git commit -m "feat: add jti claim to access tokens"
```

---

### Task 5: Per-user refresh-token index

**Files:**
- Modify: `app/services/auth_service.py` (`create_refresh_token`, `verify_and_rotate_refresh_token`)
- Test: `tests/test_auth_service.py`

**Interfaces:**
- Produces: `create_refresh_token(user_id, redis)` also `SADD`s the hash to `refresh_user:{user_id}` and sets that set's TTL. `verify_and_rotate_refresh_token` `SREM`s the old hash from the set before minting the replacement. Return signatures unchanged.
- Consumes (by Task 6): the `refresh_user:{uid}` set membership.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth_service.py  (append)
@pytest.mark.asyncio
async def test_create_refresh_token_adds_to_user_index():
    redis = AsyncMock()
    user_id = uuid.uuid4()
    raw = await create_refresh_token(user_id, redis)
    expected_hash = hashlib.sha256(raw.encode()).hexdigest()
    redis.setex.assert_awaited_once()
    redis.sadd.assert_awaited_once_with(f"refresh_user:{user_id}", expected_hash)
    redis.expire.assert_awaited_once()  # TTL set on the index set


@pytest.mark.asyncio
async def test_rotate_removes_old_hash_from_user_index():
    user_id = uuid.uuid4()
    old_raw = "old-token"
    old_hash = hashlib.sha256(old_raw.encode()).hexdigest()
    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=str(user_id))

    db = MagicMock()
    user = User()
    user.id = user_id
    user.email = "a@rutgers.edu"
    user.role = UserRole.student
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=result)

    from app import config as app_config
    app_config.settings.jwt_private_key = app_config.settings.jwt_private_key or _TEST_PRIV
    await verify_and_rotate_refresh_token(old_raw, redis, db)
    redis.srem.assert_awaited_once_with(f"refresh_user:{user_id}", old_hash)
```

Add this module-level constant near the top of `tests/test_auth_service.py` (used to guarantee a signing key exists for the rotate test):

```python
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

_TEST_PRIV = _rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.TraditionalOpenSSL,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth_service.py -v -k "user_index"`
Expected: FAIL — `sadd`/`srem`/`expire` not awaited (methods never called yet).

- [ ] **Step 3: Write minimal implementation**

In `app/services/auth_service.py`, update both functions:

```python
async def create_refresh_token(user_id: uuid.UUID, redis) -> str:
    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await redis.setex(f"refresh:{token_hash}", _REFRESH_TOKEN_EXPIRE_SECONDS, str(user_id))
    await redis.sadd(f"refresh_user:{user_id}", token_hash)
    await redis.expire(f"refresh_user:{user_id}", _REFRESH_TOKEN_EXPIRE_SECONDS)
    return raw_token


async def verify_and_rotate_refresh_token(
    token: str, redis, db: AsyncSession
) -> tuple[str, str]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    key = f"refresh:{token_hash}"
    user_id_str = await redis.getdel(key)
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")
    await redis.srem(f"refresh_user:{user_id_str}", token_hash)
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id_str)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    access_token = create_access_token(user)
    new_refresh_token = await create_refresh_token(user.id, redis)
    return access_token, new_refresh_token
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth_service.py -v -k "user_index or rotate or refresh"`
Expected: PASS (including the existing rotation tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/auth_service.py tests/test_auth_service.py
git commit -m "feat: maintain per-user refresh-token index in redis"
```

---

### Task 6: `POST /auth/logout` revokes all refresh tokens

**Files:**
- Create: `revoke_all_refresh_tokens` in `app/services/auth_service.py`
- Modify: `app/routers/auth.py` (add `logout` route)
- Test: `tests/test_auth_router.py`

**Interfaces:**
- Consumes: `refresh_user:{uid}` index from Task 5.
- Produces: `revoke_all_refresh_tokens(user_id, redis) -> None` (SMEMBERS → DEL each `refresh:{hash}` + DEL the set); `POST /api/v1/auth/logout` (requires auth) → **204 No Content**, idempotent.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth_router.py  (append)
def test_logout_revokes_all_refresh_tokens(client):
    from app import config as app_config
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
    from jose import jwt as _jwt

    priv = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    user = make_user()
    user.role = UserRole.student
    token = _jwt.encode({"sub": str(user.id)}, priv_pem, algorithm="RS256")

    redis = AsyncMock()
    redis.smembers = AsyncMock(return_value={"h1", "h2"})
    app.state.redis = redis

    orig_pub = app_config.settings.jwt_public_key
    app_config.settings.jwt_public_key = pub_pem
    # Override the auth dependency directly so no DB user row is required.
    from app.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    finally:
        app_config.settings.jwt_public_key = orig_pub
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 204
    redis.smembers.assert_awaited_once_with(f"refresh_user:{user.id}")
    delete_targets = {c.args[0] for c in redis.delete.await_args_list}
    assert "refresh:h1" in delete_targets
    assert "refresh:h2" in delete_targets
    assert f"refresh_user:{user.id}" in delete_targets


def test_logout_requires_auth(client):
    resp = client.post("/api/v1/auth/logout")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth_router.py -v -k logout`
Expected: FAIL with 404 (route does not exist yet).

- [ ] **Step 3: Write minimal implementation**

Add to `app/services/auth_service.py`:

```python
async def revoke_all_refresh_tokens(user_id: uuid.UUID, redis) -> None:
    set_key = f"refresh_user:{user_id}"
    hashes = await redis.smembers(set_key)
    for token_hash in hashes:
        await redis.delete(f"refresh:{token_hash}")
    await redis.delete(set_key)
```

In `app/routers/auth.py`, add the imports and route. Update the top import to include `get_current_user` and `User`:

```python
from app.deps import get_current_user
from app.models.user import User
```

```python
@router.post("/logout", status_code=204, summary="Revoke all of the caller's refresh tokens", responses=errors(401))
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    await auth_service.revoke_all_refresh_tokens(current_user.id, request.app.state.redis)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth_router.py -v -k logout`
Expected: PASS

- [ ] **Step 5: Run the full auth suites**

Run: `uv run pytest tests/test_auth_router.py tests/test_auth_service.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/auth_service.py app/routers/auth.py tests/test_auth_router.py
git commit -m "feat: POST /auth/logout revokes all refresh tokens"
```

---

### Task 7: FERPA RBAC verification tests

**Files:**
- Create: `tests/test_ferpa_rbac.py`

**Interfaces:**
- Consumes: `professor`, `student`, `db_session`, `client`, `clean_tables` fixtures from `conftest.py`; `grading_service._build_transcript`.
- Produces: proof that cross-tenant access returns 404 and that transcripts sent to Gemini carry no student PII.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ferpa_rbac.py
import uuid
from datetime import datetime, timezone

import pytest

from app.models.course import Course
from app.models.message import Message, MessageRole
from app.services.grading_service import _build_transcript


async def _make_course(db_session, professor_user):
    course = Course(
        id=uuid.uuid4(),
        title="Course",
        professor_id=professor_user.id,
        class_code=uuid.uuid4().hex[:6].upper().replace("0", "A"),
        semester="Fall 2026",
        is_active=True,
    )
    db_session.add(course)
    await db_session.commit()
    return course


@pytest.mark.asyncio
async def test_professor_cannot_read_other_professors_course(clean_tables, client, db_session, professor, rsa_keys):
    prof_a, _ = professor
    private_pem, _ = rsa_keys
    # A second professor
    from app.models.user import User, UserRole
    from tests.conftest import _make_token
    prof_b = User(
        id=uuid.uuid4(), google_uid=f"prof-{uuid.uuid4().hex}",
        email=f"b-{uuid.uuid4().hex[:8]}@test.edu", role=UserRole.professor,
        is_verified=False, display_name="B",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(prof_b)
    await db_session.commit()
    token_b = _make_token(prof_b.id, private_pem)

    course = await _make_course(db_session, prof_a)
    resp = await client.get(f"/api/v1/courses/{course.id}", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_student_cannot_read_other_students_session(clean_tables, client, db_session, student, rsa_keys):
    from app.models.enrollment import Enrollment
    from app.models.disease import Disease
    from app.models.unit import Unit, UnitStatus
    from app.models.session import Session, SessionStatus
    from app.models.user import User, UserRole
    from tests.conftest import _make_token

    stu_a, _ = student
    private_pem, _ = rsa_keys
    stu_b = User(
        id=uuid.uuid4(), google_uid=f"stu-{uuid.uuid4().hex}",
        email=f"b-{uuid.uuid4().hex[:8]}@test.edu", role=UserRole.student,
        is_verified=True, display_name="B",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(stu_b)
    await db_session.commit()
    token_b = _make_token(stu_b.id, private_pem)

    # A professor + course + unit + disease + a session owned by student A
    prof = User(
        id=uuid.uuid4(), google_uid=f"prof-{uuid.uuid4().hex}",
        email=f"p-{uuid.uuid4().hex[:8]}@test.edu", role=UserRole.professor,
        is_verified=False, display_name="P",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(prof)
    await db_session.commit()
    course = await _make_course(db_session, prof)
    unit = Unit(id=uuid.uuid4(), course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.commit()
    disease = Disease(id=uuid.uuid4(), unit_id=unit.id, name="MDD", category="mood", difficulty_tier=1)
    db_session.add(disease)
    await db_session.commit()
    session = Session(
        id=uuid.uuid4(), user_id=stu_a.id, course_id=course.id, disease_id=disease.id,
        status=SessionStatus.active, turn_count=0, started_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get(f"/api/v1/sessions/{session.id}", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 404


def test_transcript_contains_no_student_pii():
    messages = [
        Message(id=uuid.uuid4(), session_id=uuid.uuid4(), role=MessageRole.student,
                content="Hello, I am worried.", sent_at=datetime.now(timezone.utc)),
        Message(id=uuid.uuid4(), session_id=uuid.uuid4(), role=MessageRole.patient,
                content="I feel low.", sent_at=datetime.now(timezone.utc)),
    ]
    transcript = _build_transcript(messages)
    # Only generic speaker labels — no names, emails, or user ids.
    assert "Student:" in transcript
    assert "Patient:" in transcript
    assert "@" not in transcript
```

Note on field names: verify `Disease`, `Unit`, `Session`, `Course` constructor kwargs against the models before running; adjust any that differ (e.g. `class_code` generation). The `_make_course` helper builds a 6-char code with no `0`.

- [ ] **Step 2: Run tests to verify they fail (or reveal model mismatches)**

Run: `uv run pytest tests/test_ferpa_rbac.py -v`
Expected: The transcript test PASSES immediately (behavior already correct); the two RBAC tests should PASS if the existing 404 checks hold. If any construction error appears, fix the model kwargs. If an RBAC test unexpectedly returns 200, that is a real finding — stop and report.

- [ ] **Step 3: (No implementation expected)**

These are verification tests over existing behavior. If they pass, the RBAC posture is confirmed. If a cross-tenant test fails with 200/403, escalate — the router's ownership check needs a fix and that becomes a new task.

- [ ] **Step 4: Commit**

```bash
git add tests/test_ferpa_rbac.py
git commit -m "test: FERPA cross-tenant RBAC and transcript de-identification"
```

---

### Task 8: FERPA compliance documentation

**Files:**
- Create: `docs/ferpa-compliance.md`

- [ ] **Step 1: Write the document**

```markdown
# FERPA Compliance Measures

PocketPatient handles student education records (session transcripts, scores).
This documents the controls that keep that data access-controlled and minimized.

## Role-based access control
- Every protected route requires a valid RS256 JWT (`app/deps.py: get_current_user`).
- Role gates via `require_role("professor" | "student")`.
- Professors can only access courses they own (`Course.professor_id == current_user.id`)
  and, transitively, only the units/sessions/scores under those courses.
- Students can only access their own sessions (`Session.user_id == current_user.id`).

## Existence hiding (404, not 403)
Ownership failures return **404 Not Found**, never 403, so a caller cannot tell
whether a resource they don't own exists. Verified by `tests/test_ferpa_rbac.py`.

## LLM grading de-identification
Transcripts sent to Gemini for grading are built by
`grading_service._build_transcript`, which labels every turn as generic
"Student:" / "Patient:" — no names, emails, or user IDs are included.
Verified by `test_transcript_contains_no_student_pii`.

## PII minimization in API responses
- Session/score responses expose only pedagogical fields (diagnosis, rubric, feedback).
- No student email/display name is embedded in another user's data views.

## Authentication token handling
- Access tokens: short-lived (15 min) RS256 JWTs carrying a `jti` for traceability.
- Refresh tokens: only SHA-256 **hashes** are stored (in Redis), single-use with
  rotation; `POST /api/v1/auth/logout` revokes all of a user's refresh tokens.
- No cookies are used (bearer-header auth), so cookie security flags are N/A.

## Rate limiting
Per-endpoint limits (auth 10/min, messages 30/min, analytics 60/min, others
100/min) mitigate scraping and brute-force against these records.
```

- [ ] **Step 2: Verify referenced facts**

Confirm each referenced symbol exists: `app/deps.py: get_current_user`/`require_role`, `grading_service._build_transcript`, the `/auth/logout` route (Task 6), and the rate-limit tiers (Task 3). Fix any drift.

- [ ] **Step 3: Commit**

```bash
git add docs/ferpa-compliance.md
git commit -m "docs: FERPA compliance measures"
```

---

### Final verification

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 2: Confirm OpenAPI still builds**

Run: `uv run pytest tests/test_openapi_docs.py -q`
Expected: PASS (the new `/auth/logout` route is documented with a 401 error response).

## Self-Review Notes

- **Spec coverage:** Task 1 → sanitization + rate limiting; Tasks 4–6 → JWT jti/index/logout; Tasks 7–8 → FERPA tests + doc. Refresh-token single-use rotation already existed (confirmed in audit) — Task 5 adds only the per-user index needed for logout-all. Cookie-flags item is explicitly N/A (documented in Task 8).
- **Scope note:** Unit labels come from parsed disease documents (no direct create schema), so sanitization is scoped to student free-text + `CourseCreate.title`; extending to document-parsed labels is out of scope.
- **Model kwargs caveat:** Task 7's fixtures construct several ORM models directly — the plan flags verifying constructor kwargs against the current models before running.
