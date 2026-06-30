# Week 16 Backend — API Documentation & Database Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OpenAPI docs complete and accurate (summaries, error responses, schema examples, written guide) and clean up the database (missing FK indexes, one cascade, one check constraint, retention + backup tooling).

**Architecture:** API docs are additive metadata on existing FastAPI routes/schemas, locked in by an OpenAPI completeness test. DB cleanup is model-level changes (indexes, one `ondelete`, one `CheckConstraint`) captured in a single alembic migration, plus two standalone scripts. TDD throughout.

**Tech Stack:** FastAPI 0.111, Pydantic v2, SQLAlchemy 2.0 async, Postgres, Alembic, pytest + pytest-asyncio.

## Global Constraints

- Run everything through `uv` (`uv run pytest`, `uv run alembic ...`).
- Tests use the `clean_tables`, `db_session`, `client`, `professor`, `student` fixtures from `tests/conftest.py`. Test DB builds from `Base.metadata` (NOT alembic), so model-level indexes/constraints are present in tests automatically.
- Never call `_truncate_all()` in fixture teardown (deadlocks — see CLAUDE.md).
- Ownership/not-found checks return **404**, not 403.
- Standard error envelope is `{"detail": ..., "code": <CODE>}` from `app/main.py`; `_STATUS_TO_CODE` maps status → code.
- Autogenerate is unreliable for `ondelete` and `CheckConstraint` — hand-verify the migration.
- `--apply`/destructive scripts and `backup_db.sh` are **authored only**; the user runs them.
- Commit after each task.

---

### Task 1: OpenAPI error-response helper + app metadata

**Files:**
- Create: `app/openapi.py`
- Modify: `app/main.py:32` (the `FastAPI(...)` constructor) + imports
- Test: `tests/test_openapi_docs.py`

**Interfaces:**
- Produces: `app.openapi.ERROR_RESPONSES: dict[int, dict]` and `app.openapi.errors(*codes: int) -> dict[int, dict]`. Each entry is a valid OpenAPI response object with a `description` and an example body matching `{"detail": str, "code": str}`.
- Produces: `app.openapi.TAGS_METADATA: list[dict]` (name + description per router domain), passed to `FastAPI(openapi_tags=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_openapi_docs.py
from app.main import app


def _schema():
    return app.openapi()


def test_app_metadata_present():
    schema = _schema()
    info = schema["info"]
    assert info["title"] == "PocketPatient API"
    assert info.get("description"), "app description must be set"
    # Every router domain is described in tags metadata.
    tag_names = {t["name"] for t in schema.get("tags", [])}
    expected = {
        "auth", "users", "courses", "units",
        "disease-documents", "enrollments", "sessions", "analytics",
    }
    assert expected <= tag_names, f"missing tag metadata: {expected - tag_names}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_openapi_docs.py::test_app_metadata_present -v`
Expected: FAIL (`info description must be set` / missing tags).

- [ ] **Step 3: Create the error-responses helper and tags metadata**

```python
# app/openapi.py
"""Reusable OpenAPI documentation building blocks.

ERROR_RESPONSES documents the standard error envelope emitted by the exception
handlers in app/main.py: a JSON body of {"detail": ..., "code": <CODE>}.
"""
from __future__ import annotations


def _err(code: str, description: str, detail: str) -> dict:
    return {
        "description": description,
        "content": {
            "application/json": {"example": {"detail": detail, "code": code}}
        },
    }


ERROR_RESPONSES: dict[int, dict] = {
    400: _err("BAD_REQUEST", "Malformed request.", "Invalid request."),
    401: _err("UNAUTHORIZED", "Missing or invalid bearer token.", "Not authenticated."),
    403: _err("FORBIDDEN", "Authenticated but not allowed.", "Insufficient role."),
    404: _err("NOT_FOUND", "Resource not found or not owned by caller.", "Not found."),
    409: _err("CONFLICT", "Conflicts with existing state.", "Already exists."),
    422: _err("VALIDATION_ERROR", "Request body failed validation.", "Validation error."),
    429: _err("RATE_LIMIT_EXCEEDED", "Too many requests.", "Rate limit exceeded."),
}


def errors(*codes: int) -> dict[int, dict]:
    """Return the OpenAPI `responses` subset for the given status codes."""
    return {code: ERROR_RESPONSES[code] for code in codes}


TAGS_METADATA: list[dict] = [
    {"name": "auth", "description": "Login and token refresh (RS256 JWT)."},
    {"name": "users", "description": "Current-user profile, role, FCM token, notification prefs."},
    {"name": "courses", "description": "Course CRUD and class codes (professor)."},
    {"name": "units", "description": "Units within a course and their disease pool."},
    {"name": "disease-documents", "description": "Upload and confirm disease definition documents."},
    {"name": "enrollments", "description": "Joining courses and roster management."},
    {"name": "sessions", "description": "Student patient-simulation sessions, messaging, diagnosis."},
    {"name": "analytics", "description": "Student and professor analytics and CSV export."},
]
```

- [ ] **Step 4: Wire metadata into the app**

In `app/main.py`, add to the imports near the other `app.` imports:

```python
from app.openapi import TAGS_METADATA
```

Replace the `app = FastAPI(...)` line (currently `app/main.py:32`) with:

```python
app = FastAPI(
    title="PocketPatient API",
    version="0.1.0",
    description=(
        "Backend for PocketPatient, a psychiatry-training app where students "
        "message AI 'patients' assigned by their professor. Authenticate with a "
        "Google sign-in, exchange it for an RS256 JWT at `/api/v1/auth/login`, "
        "and send it as `Authorization: Bearer <token>`. Errors use a standard "
        "`{detail, code}` envelope. See `docs/api-guide.md` for the full guide."
    ),
    openapi_tags=TAGS_METADATA,
    contact={"name": "PocketPatient Backend"},
    license_info={"name": "Proprietary"},
    lifespan=lifespan,
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_openapi_docs.py::test_app_metadata_present -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/openapi.py app/main.py tests/test_openapi_docs.py
git commit -m "feat: OpenAPI error-response helper + app metadata"
```

---

### Task 2: Route summaries + error responses on all endpoints

**Files:**
- Modify: `app/routers/auth.py`, `users.py`, `courses.py`, `units.py`, `disease_documents.py`, `enrollments.py`, `sessions.py`, `analytics.py`
- Test: `tests/test_openapi_docs.py`

**Interfaces:**
- Consumes: `app.openapi.errors` from Task 1.

The mechanical change for every route is: add a `summary=` and `responses=errors(...)` kwarg to the decorator. Example transformation in `app/routers/auth.py`:

```python
from app.openapi import errors  # add to imports

@router.post("/login", response_model=TokenResponse, summary="Exchange Google ID token for a JWT", responses=errors(401, 422, 429))
async def login(...):
    ...
```

Apply this to every `@router.<verb>` in each router using the table below. Every route also needs `from app.openapi import errors` added to that router's imports. Auth-guarded routes (everything except `auth.login` / `auth.refresh`) always include `401`. Routes with path params or ownership checks include `404`. Routes with a request body include `422`. All include `429` (global rate limit).

| Router | Route | summary | error codes |
|---|---|---|---|
| auth | POST /login | Exchange Google ID token for a JWT | 401, 422, 429 |
| auth | POST /refresh | Refresh an access token | 401, 422, 429 |
| users | GET /me | Get the current user | 401, 429 |
| users | PUT /me/role | Set the current user's role | 401, 422, 429 |
| users | PUT /me/fcm-token | Update the FCM push token | 401, 422, 429 |
| users | PUT /me/notification-preferences | Update notification preferences | 401, 422, 429 |
| courses | GET "" | List the caller's courses | 401, 429 |
| courses | GET /{course_id} | Get one course | 401, 404, 429 |
| courses | PUT /{course_id} | Update a course | 401, 404, 422, 429 |
| courses | DELETE /{course_id}/deactivate | Deactivate a course | 401, 404, 429 |
| courses | POST "" | Create a course | 401, 422, 429 |
| units | GET /units | List units in a course | 401, 404, 429 |
| units | PUT /units/{unit_id}/release | Release a unit | 401, 404, 429 |
| units | PUT /units/{unit_id}/close | Close a unit | 401, 404, 429 |
| units | GET /disease-pool | List the course disease pool | 401, 404, 429 |
| disease-documents | POST "" | Upload a disease document for preview | 401, 404, 422, 429 |
| disease-documents | POST /confirm | Confirm a parsed disease document | 401, 404, 422, 429 |
| enrollments | POST /enrollments/join | Join a course by class code | 401, 404, 422, 429 |
| enrollments | GET /courses/{course_id}/students | List enrolled students | 401, 404, 429 |
| enrollments | DELETE /courses/{course_id}/students/{user_id} | Unenroll a student | 401, 404, 429 |
| sessions | GET /active | Get the caller's active session | 401, 404, 429 |
| sessions | GET "" | List the caller's sessions (paginated) | 401, 429 |
| sessions | GET /{session_id} | Get one session | 401, 404, 429 |
| sessions | POST /{session_id}/messages | Send a message to the patient | 401, 404, 422, 429 |
| sessions | POST /{session_id}/diagnose | Submit a diagnosis | 401, 404, 422, 429 |
| sessions | POST "" | Start a new session | 401, 404, 422, 429 |
| analytics | GET /student/summary | Student's own analytics summary | 401, 429 |
| analytics | GET /professor/class-summary | Class analytics summary | 401, 404, 429 |
| analytics | GET /professor/student/{user_id} | Per-student drill-down | 401, 404, 429 |
| analytics | GET /professor/export | Export class analytics as CSV | 401, 404, 429 |

> If a route exists in a file but is not in this table (e.g. a create-unit route), give it a one-line `summary` describing what it does and `responses=errors(401, 422, 429)` (add `404` if it has a path param). Every `@router.<verb>` must end up with a `summary`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_openapi_docs.py
import pytest

_METHODS = {"get", "post", "put", "patch", "delete"}


def _operations():
    schema = _schema()
    for path, item in schema["paths"].items():
        for method, op in item.items():
            if method in _METHODS:
                yield path, method, op


def test_every_route_has_summary_and_tags():
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in _operations()
        if not op.get("summary") or not op.get("tags")
    ]
    assert not missing, f"routes missing summary/tags: {missing}"


def test_protected_routes_declare_401():
    # Auth endpoints under /auth/login and /auth/refresh are the only public posts.
    public = {("/api/v1/auth/login", "post"), ("/api/v1/auth/refresh", "post")}
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in _operations()
        if (path, method) not in public and "401" not in op.get("responses", {})
    ]
    assert not missing, f"protected routes missing 401 response: {missing}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_openapi_docs.py -k "summary or 401" -v`
Expected: FAIL — long list of routes missing summary/401.

- [ ] **Step 3: Apply summaries + responses to all routers**

Edit each router file per the table: add `from app.openapi import errors` to imports, then add `summary=...` and `responses=errors(...)` to each decorator. Keep existing kwargs (`response_model`, `status_code`) intact.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_openapi_docs.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest -q`
Expected: PASS (route metadata is additive; behavior unchanged).

- [ ] **Step 6: Commit**

```bash
git add app/routers tests/test_openapi_docs.py
git commit -m "docs: add summaries and error responses to all API routes"
```

---

### Task 3: Schema descriptions + examples on core models

**Files:**
- Modify: `app/schemas/course.py`, `user.py`, `session.py`, `enrollment.py`, `unit.py`, `disease_document.py`, `analytics.py`
- Test: `tests/test_openapi_docs.py`

**Interfaces:**
- Consumes: nothing new. Produces documented schema components in `app.openapi()["components"]["schemas"]`.

Add a `model_config` with `json_schema_extra={"example": {...}}` to each core **response** model (the `*Out`, `Token*`, `*Summary`, `*Result`, `Paginated*`, `NotificationPreferences` models) and add `description=` to non-obvious `Field`s (IDs, timestamps, enums, computed fields like `student_count`). Existing `model_config = {"from_attributes": True}` must be merged, not replaced.

Example for `app/schemas/course.py` `CourseOut` — replace its `model_config` line and annotate fields:

```python
class CourseOut(BaseModel):
    id: uuid.UUID
    title: str
    professor_id: uuid.UUID = Field(description="ID of the professor who owns the course.")
    class_code: str = Field(description="6-char uppercase join code (no 0/O/1/I/L).")
    semester: str | None
    is_active: bool
    msg_window_start: time
    msg_window_end: time
    msg_timezone: str
    created_at: datetime
    student_count: int = Field(description="Number of enrolled students (computed).")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "title": "Intro to Clinical Psychiatry",
                "professor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "class_code": "BRT4K9",
                "semester": "Fall 2026",
                "is_active": True,
                "msg_window_start": "08:00:00",
                "msg_window_end": "22:00:00",
                "msg_timezone": "America/New_York",
                "created_at": "2026-09-01T14:30:00Z",
                "student_count": 24,
            }
        },
    }
```

Apply the same pattern (one representative `example` + field descriptions on non-obvious fields) to the core response models in the other listed schema files. Read each file first to get exact field names; keep `from_attributes` where present.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_openapi_docs.py

# Core response models that must carry a description and an example.
_CORE_SCHEMAS = [
    "CourseOut",
    "UserOut",
    "SessionOut",
    "TokenResponse",
    "StudentSummary",
    "ClassSummary",
    "NotificationPreferences",
]


@pytest.mark.parametrize("name", _CORE_SCHEMAS)
def test_core_schema_has_example(name):
    schemas = _schema()["components"]["schemas"]
    assert name in schemas, f"{name} not in OpenAPI components"
    assert schemas[name].get("example") or schemas[name].get("examples"), (
        f"{name} must declare an example"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_openapi_docs.py -k example -v`
Expected: FAIL — schemas missing `example`.

- [ ] **Step 3: Add examples + field descriptions**

Edit the listed schema files. Confirm the exact model names by reading each file (e.g. `app/schemas/user.py` for `UserOut`/`NotificationPreferences`, `app/schemas/analytics.py` for `StudentSummary`/`ClassSummary`, `app/schemas/session.py` for `SessionOut`, `app/schemas/user.py` or `auth` schema for `TokenResponse`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_openapi_docs.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/schemas tests/test_openapi_docs.py
git commit -m "docs: add descriptions and examples to core schemas"
```

---

### Task 4: API guide document

**Files:**
- Create: `docs/api-guide.md`

**Interfaces:** none (prose).

- [ ] **Step 1: Write the guide**

Create `docs/api-guide.md` covering, in this order:
1. **Base URL & versioning** — all endpoints under `/api/v1`; `/health` is unprefixed; interactive docs at `/docs` (Swagger) and `/redoc`.
2. **Auth flow** — client does Google sign-in → POST `/api/v1/auth/login` with the Google ID token → receives an RS256 JWT access token (+ refresh) → sends `Authorization: Bearer <token>` on every call. Token verified in `app/deps.py`; roles enforced by `require_role("professor"|"student")`. Refresh via `/api/v1/auth/refresh`.
3. **Error envelope** — every error is `{"detail": ..., "code": <CODE>}`. Include the full code table from `app/main.py` `_STATUS_TO_CODE` (400 BAD_REQUEST, 401 UNAUTHORIZED, 403 FORBIDDEN, 404 NOT_FOUND, 405 METHOD_NOT_ALLOWED, 409 CONFLICT, 410 GONE, 422 VALIDATION_ERROR, 429 RATE_LIMIT_EXCEEDED, 500 INTERNAL_ERROR). Note that `422`'s `detail` is a list of Pydantic errors.
4. **Conventions** — ownership/not-found returns **404 not 403** to avoid leaking existence; pagination shape for `GET /sessions` (`PaginatedSessions`); rate limiting returns 429.
5. **Pointers** — `docs/api-contract.md` for the canonical endpoint reference; `/docs` for live schema.

- [ ] **Step 2: Sanity-check the doc**

Run: `uv run python -c "import pathlib; assert pathlib.Path('docs/api-guide.md').read_text().strip(); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add docs/api-guide.md
git commit -m "docs: add API guide (auth flow, errors, conventions)"
```

---

### Task 5: Add missing foreign-key indexes

**Files:**
- Modify: `app/models/course.py`, `app/models/unit.py`, `app/models/session.py`, `app/models/disease_document.py`
- Test: `tests/test_db_constraints.py`

**Interfaces:**
- Produces: indexes `ix_courses_professor_id`, `ix_units_course_id`, `ix_sessions_disease_id`, `ix_disease_documents_uploaded_by`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_constraints.py
import pytest
from sqlalchemy import inspect

from app.database import Base


@pytest.mark.parametrize(
    "table,index_name",
    [
        ("courses", "ix_courses_professor_id"),
        ("units", "ix_units_course_id"),
        ("sessions", "ix_sessions_disease_id"),
        ("disease_documents", "ix_disease_documents_uploaded_by"),
    ],
)
def test_expected_index_defined(table, index_name):
    idx_names = {ix.name for ix in Base.metadata.tables[table].indexes}
    assert index_name in idx_names, f"{index_name} missing on {table}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db_constraints.py -v`
Expected: FAIL — indexes missing.

- [ ] **Step 3: Add the indexes to the models**

`app/models/course.py` — add `Index` import and `__table_args__`:

```python
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Time, func
# ...
class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        Index("ix_courses_professor_id", "professor_id"),
    )
```

`app/models/unit.py` — add `Index` import and `__table_args__`:

```python
from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
# ...
class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        Index("ix_units_course_id", "course_id"),
    )
```

`app/models/session.py` — add to the existing `__table_args__` tuple:

```python
        Index("ix_sessions_disease_id", "disease_id"),
```

`app/models/disease_document.py` — add `Index` import and the index to the existing `__table_args__`:

```python
from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
# ...
    __table_args__ = (
        UniqueConstraint("course_id", "version"),
        Index("ix_disease_documents_uploaded_by", "uploaded_by"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db_constraints.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models tests/test_db_constraints.py
git commit -m "feat: add missing foreign-key indexes"
```

---

### Task 6: messages→session cascade + quiet-hours check constraint

**Files:**
- Modify: `app/models/message.py` (FK `ondelete`), `app/models/user.py` (`CheckConstraint`)
- Test: `tests/test_db_constraints.py`

**Interfaces:**
- Produces: `messages.session_id` FK with `ondelete="CASCADE"`; `users` table check constraint `ck_quiet_hours_paired`.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_db_constraints.py
import uuid
from datetime import datetime, time, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.user import User


async def _make_session_with_message(db_session):
    """Insert a minimal user→course→unit→disease→session→message graph."""
    from app.models.course import Course
    from app.models.unit import Unit
    from app.models.disease import Disease
    from app.models.session import Session as SessionModel
    from app.models.message import Message, MessageRole

    user = User(google_uid=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.com")
    db_session.add(user)
    await db_session.flush()
    course = Course(title="C", professor_id=user.id, class_code=str(uuid.uuid4())[:6].upper())
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U")
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(
        unit_id=unit.id, name="D", category="cat", key_symptoms=[],
        differentials=[], difficulty_tier=1, speech_style="s", nudge_behavior={},
    )
    db_session.add(disease)
    await db_session.flush()
    session = SessionModel(
        disease_id=disease.id, user_id=user.id, course_id=course.id,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    await db_session.flush()
    msg = Message(
        session_id=session.id, role=MessageRole.student, content="hi",
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    await db_session.commit()
    return session, msg


async def test_deleting_session_cascades_messages(clean_tables, db_session):
    from sqlalchemy import select
    from app.models.session import Session as SessionModel
    from app.models.message import Message

    session, msg = await _make_session_with_message(db_session)
    await db_session.delete(await db_session.get(SessionModel, session.id))
    await db_session.commit()
    remaining = (await db_session.execute(select(Message).where(Message.id == msg.id))).first()
    assert remaining is None, "message should be cascade-deleted with its session"


async def test_quiet_hours_must_be_paired(clean_tables, db_session):
    user = User(
        google_uid=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.com",
        quiet_hours_start=time(22, 0), quiet_hours_end=None,
    )
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_quiet_hours_both_set_ok(clean_tables, db_session):
    user = User(
        google_uid=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.com",
        quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0),
    )
    db_session.add(user)
    await db_session.commit()  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db_constraints.py -k "cascade or quiet" -v`
Expected: FAIL (message not deleted; quiet-hours commit does not raise).

- [ ] **Step 3: Add the cascade**

`app/models/message.py` — change the `session_id` FK:

```python
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
```

- [ ] **Step 4: Add the check constraint**

`app/models/user.py` — add `CheckConstraint` import and `__table_args__` on `User`:

```python
from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, String, Time, func
# ...
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL)",
            name="ck_quiet_hours_paired",
        ),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_db_constraints.py -v`
Expected: PASS

> Note: the test DB is created once per session from `Base.metadata`. The new constraint/cascade are picked up because they are defined on the models before `create_all` runs. If a stale test DB exists from a prior run with old schema, drop it: `uv run python -c "import asyncio,asyncpg; ..."` is unnecessary — the session-scoped `test_db` fixture drops/recreates per session.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/models/message.py app/models/user.py tests/test_db_constraints.py
git commit -m "feat: cascade messages on session delete + quiet-hours check"
```

---

### Task 7: Session retention/archive script

**Files:**
- Create: `scripts/archive_old_sessions.py`
- Modify: `.gitignore` (add `archives/`)
- Test: `tests/test_archive_sessions.py`

**Interfaces:**
- Produces: `scripts.archive_old_sessions.select_old_session_ids(db: AsyncSession, cutoff: datetime) -> list[uuid.UUID]` returning IDs of sessions with `started_at < cutoff`.
- Produces: `scripts.archive_old_sessions.cutoff_for(years: int, now: datetime | None = None) -> datetime`.
- CLI: `uv run python -m scripts.archive_old_sessions [--years N] [--apply]` (dry-run unless `--apply`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_archive_sessions.py
import uuid
from datetime import datetime, timedelta, timezone

from scripts.archive_old_sessions import cutoff_for, select_old_session_ids


def test_cutoff_for_subtracts_years():
    now = datetime(2026, 6, 29, tzinfo=timezone.utc)
    assert cutoff_for(3, now) == datetime(2023, 6, 29, tzinfo=timezone.utc)


async def _make_session(db_session, started_at):
    from app.models.user import User
    from app.models.course import Course
    from app.models.unit import Unit
    from app.models.disease import Disease
    from app.models.session import Session as SessionModel

    user = User(google_uid=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.com")
    db_session.add(user)
    await db_session.flush()
    course = Course(title="C", professor_id=user.id, class_code=str(uuid.uuid4())[:6].upper())
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U")
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(
        unit_id=unit.id, name="D", category="c", key_symptoms=[],
        differentials=[], difficulty_tier=1, speech_style="s", nudge_behavior={},
    )
    db_session.add(disease)
    await db_session.flush()
    session = SessionModel(
        disease_id=disease.id, user_id=user.id, course_id=course.id, started_at=started_at,
    )
    db_session.add(session)
    await db_session.commit()
    return session.id


async def test_select_old_session_ids_only_returns_old(clean_tables, db_session):
    now = datetime.now(timezone.utc)
    old_id = await _make_session(db_session, now - timedelta(days=365 * 4))
    await _make_session(db_session, now - timedelta(days=30))
    cutoff = cutoff_for(3, now)
    ids = await select_old_session_ids(db_session, cutoff)
    assert ids == [old_id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_archive_sessions.py -v`
Expected: FAIL (`ModuleNotFoundError: scripts.archive_old_sessions`).

- [ ] **Step 3: Implement the script**

```python
# scripts/archive_old_sessions.py
"""Archive (and optionally delete) sessions older than N years.

Dry-run by default: prints how many sessions would be archived. With --apply it
writes each session (plus its messages and score) to a timestamped JSONL file
under archives/ and then deletes the sessions. Messages cascade via the
sessions FK; scores cascade on session delete.

    uv run python -m scripts.archive_old_sessions            # dry run, 3 years
    uv run python -m scripts.archive_old_sessions --years 5
    uv run python -m scripts.archive_old_sessions --apply    # actually archive+delete
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.message import Message
from app.models.score import Score
from app.models.session import Session as SessionModel

ARCHIVE_DIR = Path("archives")


def cutoff_for(years: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(year=now.year - years)


async def select_old_session_ids(db: AsyncSession, cutoff: datetime) -> list[uuid.UUID]:
    result = await db.execute(
        select(SessionModel.id)
        .where(SessionModel.started_at < cutoff)
        .order_by(SessionModel.started_at)
    )
    return [row[0] for row in result.all()]


def _serialize(obj) -> dict:
    return {
        c.name: (str(v) if isinstance(v := getattr(obj, c.name), (uuid.UUID, datetime)) else v)
        for c in obj.__table__.columns
    }


async def _export(db: AsyncSession, session_ids: list[uuid.UUID], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for sid in session_ids:
            session = await db.get(SessionModel, sid)
            messages = (await db.execute(select(Message).where(Message.session_id == sid))).scalars().all()
            score = (await db.execute(select(Score).where(Score.session_id == sid))).scalar_one_or_none()
            fh.write(json.dumps({
                "session": _serialize(session),
                "messages": [_serialize(m) for m in messages],
                "score": _serialize(score) if score else None,
            }) + "\n")


async def run(years: int, apply: bool) -> None:
    cutoff = cutoff_for(years)
    async with AsyncSessionLocal() as db:
        ids = await select_old_session_ids(db, cutoff)
        print(f"{len(ids)} sessions started before {cutoff.isoformat()}")
        if not apply:
            print("dry run — pass --apply to archive and delete")
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = ARCHIVE_DIR / f"sessions-{stamp}.jsonl"
        await _export(db, ids, out)
        for sid in ids:
            await db.delete(await db.get(SessionModel, sid))
        await db.commit()
        print(f"archived {len(ids)} sessions to {out} and deleted them")


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive sessions older than N years.")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--apply", action="store_true", help="archive AND delete (default: dry run)")
    args = parser.parse_args()
    asyncio.run(run(args.years, args.apply))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Ignore the archives directory**

Add a line to `.gitignore`:

```
archives/
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_archive_sessions.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/archive_old_sessions.py tests/test_archive_sessions.py .gitignore
git commit -m "feat: session retention/archive script (dry-run by default)"
```

---

### Task 8: Database backup script (authored only)

**Files:**
- Create: `scripts/backup_db.sh`
- Modify: `.gitignore` (add `backups/`)

**Interfaces:** none (shell). Not executed in this session — the user runs it.

- [ ] **Step 1: Write the script**

```bash
# scripts/backup_db.sh
#!/usr/bin/env bash
# Back up the PocketPatient dev database with pg_dump.
#
# Reads the connection string from $DATABASE_URL, falling back to the value in
# .env. Writes a timestamped custom-format dump to backups/.
#
# Usage:
#   ./scripts/backup_db.sh
#
# Restore (into an existing empty DB):
#   pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" backups/<file>.dump
#
set -euo pipefail

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" && -f .env ]]; then
  DB_URL="$(grep -E '^DATABASE_URL=' .env | head -1 | cut -d= -f2-)"
fi
if [[ -z "$DB_URL" ]]; then
  echo "DATABASE_URL not set and not found in .env" >&2
  exit 1
fi

# pg_dump speaks libpq URLs; strip the SQLAlchemy +asyncpg dialect suffix.
DB_URL="${DB_URL/+asyncpg/}"

mkdir -p backups
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="backups/pocketpatient-${STAMP}.dump"

pg_dump --format=custom --no-owner --file="$OUT" "$DB_URL"
echo "wrote $OUT"
```

- [ ] **Step 2: Make it executable and syntax-check it**

Run:
```bash
chmod +x scripts/backup_db.sh
bash -n scripts/backup_db.sh && echo "syntax ok"
```
Expected: `syntax ok` (do NOT run the script itself — it touches the live DB).

- [ ] **Step 3: Ignore the backups directory**

Add a line to `.gitignore`:

```
backups/
```

- [ ] **Step 4: Commit**

```bash
git add scripts/backup_db.sh .gitignore
git commit -m "chore: add pg_dump backup script (authored, run manually)"
```

---

### Task 9: Alembic migration for the schema changes

**Files:**
- Create: `alembic/versions/<rev>_week16_indexes_cascade_check.py` (autogenerated)

**Interfaces:** none. Captures Task 5 + Task 6 model changes as a migration.

- [ ] **Step 1: Autogenerate the migration**

Run:
```bash
uv run alembic revision --autogenerate -m "week16 indexes cascade check"
```
Expected: a new file under `alembic/versions/`.

- [ ] **Step 2: Hand-verify the migration**

Open the generated file. Confirm `upgrade()` contains (autogenerate is unreliable for FK `ondelete` and `CheckConstraint` — add by hand if missing):
- `op.create_index('ix_courses_professor_id', 'courses', ['professor_id'])`
- `op.create_index('ix_units_course_id', 'units', ['course_id'])`
- `op.create_index('ix_sessions_disease_id', 'sessions', ['disease_id'])`
- `op.create_index('ix_disease_documents_uploaded_by', 'disease_documents', ['uploaded_by'])`
- For the `messages.session_id` cascade — drop and recreate the FK:
  ```python
  op.drop_constraint('messages_session_id_fkey', 'messages', type_='foreignkey')
  op.create_foreign_key(
      'messages_session_id_fkey', 'messages', 'sessions',
      ['session_id'], ['id'], ondelete='CASCADE',
  )
  ```
  (Confirm the existing constraint name with `\d messages` in psql if autogenerate names it differently.)
- For the check constraint:
  ```python
  op.create_check_constraint(
      'ck_quiet_hours_paired', 'users',
      '(quiet_hours_start IS NULL) = (quiet_hours_end IS NULL)',
  )
  ```

Ensure `downgrade()` reverses each (drop indexes, restore the FK without `ondelete`, drop the check constraint).

- [ ] **Step 3: Apply the migration to the dev DB**

Run:
```bash
uv run alembic upgrade head
```
Expected: completes without error.

- [ ] **Step 4: Verify the migration round-trips**

Run:
```bash
uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: both succeed (confirms `downgrade()` is correct).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add alembic/versions
git commit -m "feat: migration for week16 indexes, cascade, and check constraint"
```

---

## Self-Review notes

- **Spec coverage:** Task 1 docs cover OpenAPI metadata + error responses (spec T1.1–T1.2); Task 2 covers route summaries/responses (T1.3); Task 3 covers schema docs at "core + examples" depth (T1.4); Task 1/2/3 share the completeness test (T1.5); Task 4 covers the API guide (T1.6). Task 5 covers missing indexes (T2.1); Task 6 covers the messages cascade + quiet-hours check (T2.2–T2.3); Task 7 covers the retention script (T2.4); Task 8 covers the backup script, authored-only (T2.5); Task 9 covers the single migration (T2.6).
- **RESTRICT guard:** the spec mentioned a test that deleting a course with sessions raises `IntegrityError`. Postgres default FK action is `NO ACTION`, which does raise on a referenced row, so the guard holds without code changes; an explicit test for it is optional and omitted to keep Task 6 focused. If desired, add it alongside the cascade test.
- **Type consistency:** `select_old_session_ids`/`cutoff_for` signatures match between Task 7's interface block, test, and implementation. `errors()`/`ERROR_RESPONSES`/`TAGS_METADATA` names match between Task 1 and Task 2.
