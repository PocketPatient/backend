# Week 3 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement course CRUD and enrollment endpoints for PocketPatient, plus integration tests against a real PostgreSQL test database.

**Architecture:** Two new routers (`courses.py`, `enrollments.py`) with inline business logic matching the existing codebase pattern. Pydantic schemas in `app/schemas/`. `CourseOut.student_count` is computed via correlated subquery — not stored. Integration tests use `httpx.AsyncClient` + a dedicated `pocketpatient_test` database, with tables created/dropped per session and truncated between tests.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, asyncpg, Pydantic v2, pytest-asyncio 0.23.7, httpx, python-jose (RS256 JWT)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `app/schemas/course.py` | CourseCreate, CourseUpdate, CourseOut |
| Create | `app/schemas/enrollment.py` | EnrollmentJoinRequest, EnrolledStudentOut |
| Create | `app/routers/courses.py` | Course CRUD endpoints + helpers |
| Create | `app/routers/enrollments.py` | Join, list students, remove student |
| Modify | `app/main.py` | Register both new routers |
| Modify | `tests/conftest.py` | Add async test DB fixtures (keep existing rsa_keys) |
| Create | `tests/test_courses_router.py` | Integration tests for course endpoints |
| Create | `tests/test_enrollments_router.py` | Integration tests for enrollment endpoints |
| Modify | `docs/api-contract.md` | Document all new endpoints |

---

## Task 1: Course & Enrollment Schemas

**Files:**
- Create: `app/schemas/course.py`
- Create: `app/schemas/enrollment.py`

No tests for pure schema files — validation is covered by the router tests.

- [ ] **Step 1: Create `app/schemas/course.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime, time

from pydantic import BaseModel


class CourseCreate(BaseModel):
    title: str
    semester: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = None
    semester: str | None = None
    msg_window_start: time | None = None
    msg_window_end: time | None = None
    msg_timezone: str | None = None


class CourseOut(BaseModel):
    id: uuid.UUID
    title: str
    professor_id: uuid.UUID
    class_code: str
    semester: str | None
    is_active: bool
    msg_window_start: time
    msg_window_end: time
    msg_timezone: str
    created_at: datetime
    student_count: int

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Create `app/schemas/enrollment.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class EnrollmentJoinRequest(BaseModel):
    class_code: str


class EnrolledStudentOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    enrolled_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 3: Commit**

```bash
git add app/schemas/course.py app/schemas/enrollment.py
git commit -m "feat: add course and enrollment Pydantic schemas"
```

---

## Task 2: Test Infrastructure

**Files:**
- Modify: `tests/conftest.py`

Before running tests, the `pocketpatient_test` database must exist. Create it once:

```bash
docker exec -it <postgres-container> psql -U postgres -c "CREATE DATABASE pocketpatient_test;"
```

Or if running Postgres locally:
```bash
psql -U postgres -c "CREATE DATABASE pocketpatient_test;"
```

- [ ] **Step 1: Replace `tests/conftest.py` with the expanded version**

The new file keeps the existing `rsa_keys` fixture unchanged and adds async test DB fixtures. `clean_tables` is opt-in (not autouse globally) — the integration test files will request it via `pytestmark`.

```python
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import config as app_config
from app.database import Base, get_db
from app.main import app
from app.models.user import User, UserRole

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient_test",
)

_test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
_TestSession = async_sessionmaker(_test_engine, expire_on_commit=False)


@pytest.fixture(scope="session")
def rsa_keys():
    """Generate a test RSA keypair. Session-scoped so it runs once per test session."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


@pytest_asyncio.fixture(scope="session")
async def test_db():
    """Create all tables once for the test session, drop them at the end."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(test_db):
    """Direct DB session for fixture setup (inserting users, etc.)."""
    async with _TestSession() as session:
        yield session


@pytest_asyncio.fixture
async def client(rsa_keys, test_db):
    """AsyncClient wired to the test app with JWT public key patched."""
    private_pem, public_pem = rsa_keys

    original_key = app_config.settings.jwt_public_key
    app_config.settings.jwt_public_key = public_pem

    async def override_get_db():
        async with _TestSession() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.state.redis = AsyncMock()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app_config.settings.jwt_public_key = original_key
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def clean_tables(test_db):
    """Truncate all data before and after each test. Request this fixture in test files."""
    async with _test_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE enrollments, courses, users CASCADE"))
    yield
    async with _test_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE enrollments, courses, users CASCADE"))


def _make_token(user_id: uuid.UUID, private_pem: str) -> str:
    return jwt.encode({"sub": str(user_id)}, private_pem, algorithm="RS256")


@pytest_asyncio.fixture
async def professor(db_session, rsa_keys):
    """Insert a professor user and return (User, JWT token)."""
    private_pem, _ = rsa_keys
    user = User(
        id=uuid.uuid4(),
        google_uid=f"prof-{uuid.uuid4().hex}",
        email=f"professor-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Test Professor",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, _make_token(user.id, private_pem)


@pytest_asyncio.fixture
async def student(db_session, rsa_keys):
    """Insert a student user and return (User, JWT token)."""
    private_pem, _ = rsa_keys
    user = User(
        id=uuid.uuid4(),
        google_uid=f"stu-{uuid.uuid4().hex}",
        email=f"student-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.student,
        is_verified=True,
        display_name="Test Student",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, _make_token(user.id, private_pem)
```

- [ ] **Step 2: Verify the test DB is reachable and the old tests still pass**

```bash
cd /Users/mahirshah/PocketPatient/backend
uv run pytest tests/test_auth_service.py tests/test_auth_router.py tests/test_users_router.py tests/test_deps.py -v
```

Expected: all existing tests pass. If any fail, stop and fix before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add async integration test infrastructure (test DB, fixtures)"
```

---

## Task 3: TDD — Course Creation (`POST /courses`)

**Files:**
- Create: `tests/test_courses_router.py` (initial)
- Create: `app/routers/courses.py` (stub → implementation)
- Modify: `app/main.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_courses_router.py`:

```python
import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")


async def test_professor_creates_course(client, professor):
    _, token = professor
    response = await client.post(
        "/api/v1/courses",
        json={"title": "Psychiatry 101", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Psychiatry 101"
    assert body["semester"] == "Fall 2026"
    assert len(body["class_code"]) == 6
    assert body["class_code"].isupper()
    assert body["student_count"] == 0
    assert body["is_active"] is True


async def test_student_cannot_create_course(client, student):
    _, token = student
    response = await client.post(
        "/api/v1/courses",
        json={"title": "Psychiatry 101", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


async def test_create_course_no_auth(client):
    response = await client.post(
        "/api/v1/courses",
        json={"title": "Psychiatry 101"},
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Run tests — expect ImportError or 404 (router doesn't exist yet)**

```bash
uv run pytest tests/test_courses_router.py -v
```

Expected: FAILED — ImportError or connection refused (courses router not registered).

- [ ] **Step 3: Create `app/routers/courses.py`**

```python
from __future__ import annotations

import random
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.user import User, UserRole
from app.schemas.course import CourseCreate, CourseOut, CourseUpdate

router = APIRouter(prefix="/courses", tags=["courses"])

_SAFE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate_class_code() -> str:
    return "".join(random.choices(_SAFE_CHARS, k=6))


def _make_course_out(course: Course, student_count: int) -> CourseOut:
    return CourseOut(
        id=course.id,
        title=course.title,
        professor_id=course.professor_id,
        class_code=course.class_code,
        semester=course.semester,
        is_active=course.is_active,
        msg_window_start=course.msg_window_start,
        msg_window_end=course.msg_window_end,
        msg_timezone=course.msg_timezone,
        created_at=course.created_at,
        student_count=student_count,
    )


async def _count_students(db: AsyncSession, course_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Enrollment).where(Enrollment.course_id == course_id)
    )
    return result.scalar_one()


def _student_count_subquery():
    return (
        select(func.count())
        .select_from(Enrollment)
        .where(Enrollment.course_id == Course.id)
        .correlate(Course)
        .scalar_subquery()
    )


@router.post("", status_code=201, response_model=CourseOut)
async def create_course(
    body: CourseCreate,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    for _ in range(10):
        code = _generate_class_code()
        existing = await db.execute(select(Course).where(Course.class_code == code))
        if existing.scalar_one_or_none() is None:
            break
    else:
        raise HTTPException(status_code=500, detail="Failed to generate unique class code")

    course = Course(
        title=body.title,
        professor_id=current_user.id,
        class_code=code,
        semester=body.semester,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return _make_course_out(course, 0)
```

- [ ] **Step 4: Register the router in `app/main.py`**

```python
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import firebase_admin
import redis.asyncio as aioredis
from fastapi import FastAPI

from app.config import settings
from app.routers import auth, users, courses


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not firebase_admin._apps and settings.firebase_project_id:
        firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="PocketPatient API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(courses.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
uv run pytest tests/test_courses_router.py::test_professor_creates_course tests/test_courses_router.py::test_student_cannot_create_course tests/test_courses_router.py::test_create_course_no_auth -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add app/routers/courses.py app/main.py tests/test_courses_router.py
git commit -m "feat: add course creation endpoint (POST /courses)"
```

---

## Task 4: TDD — Course Listing & Detail (`GET /courses`, `GET /courses/{course_id}`)

**Files:**
- Modify: `tests/test_courses_router.py`
- Modify: `app/routers/courses.py`

- [ ] **Step 1: Add failing tests to `tests/test_courses_router.py`**

Append these tests to the existing file:

```python
async def test_professor_list_courses_sees_own(client, professor):
    _, token = professor
    await client.post(
        "/api/v1/courses",
        json={"title": "Course A", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/api/v1/courses",
        json={"title": "Course B", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = await client.get("/api/v1/courses", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    titles = [c["title"] for c in response.json()]
    assert "Course A" in titles
    assert "Course B" in titles


async def test_student_list_courses_sees_enrolled(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    class_code = create_resp.json()["class_code"]
    await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    response = await client.get("/api/v1/courses", headers={"Authorization": f"Bearer {stu_token}"})
    assert response.status_code == 200
    assert any(c["title"] == "Psych 101" for c in response.json())


async def test_get_course_detail_professor(client, professor):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Detail Test", "semester": "Spring 2027"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]
    response = await client.get(
        f"/api/v1/courses/{course_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Detail Test"


async def test_get_course_detail_wrong_professor_returns_404(client, professor, rsa_keys):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Secret Course"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]

    # A second professor who doesn't own this course
    import uuid
    from datetime import datetime, timezone
    from app.models.user import User, UserRole
    from tests.conftest import _TestSession, _make_token
    private_pem, _ = rsa_keys
    other = User(
        id=uuid.uuid4(),
        google_uid=f"other-{uuid.uuid4().hex}",
        email=f"other-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Other Professor",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    async with _TestSession() as s:
        s.add(other)
        await s.commit()
    other_token = _make_token(other.id, private_pem)
    response = await client.get(
        f"/api/v1/courses/{course_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/test_courses_router.py::test_professor_list_courses_sees_own tests/test_courses_router.py::test_get_course_detail_professor -v
```

Expected: FAILED — `GET /courses` and `GET /courses/{id}` return 404 (not implemented).

Note: `test_student_list_courses_sees_enrolled` depends on the enrollment join endpoint — it will be re-run after Task 6.

- [ ] **Step 3: Add `list_courses` and `get_course` handlers to `app/routers/courses.py`**

Add after `create_course`:

```python
@router.get("", response_model=list[CourseOut])
async def list_courses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sq = _student_count_subquery()
    if current_user.role == UserRole.professor:
        stmt = (
            select(Course, sq.label("student_count"))
            .where(Course.professor_id == current_user.id)
        )
    else:
        enrolled_sq = select(Enrollment.course_id).where(Enrollment.user_id == current_user.id)
        stmt = (
            select(Course, sq.label("student_count"))
            .where(Course.id.in_(enrolled_sq))
        )
    rows = (await db.execute(stmt)).all()
    return [_make_course_out(course, count) for course, count in rows]


@router.get("/{course_id}", response_model=CourseOut)
async def get_course(
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sq = _student_count_subquery()
    result = await db.execute(
        select(Course, sq.label("student_count")).where(Course.id == course_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Course not found")
    course, count = row

    if current_user.role == UserRole.professor:
        if course.professor_id != current_user.id:
            raise HTTPException(status_code=404, detail="Course not found")
    else:
        enrolled = await db.execute(
            select(Enrollment).where(
                Enrollment.course_id == course_id,
                Enrollment.user_id == current_user.id,
            )
        )
        if enrolled.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Course not found")

    return _make_course_out(course, count)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_courses_router.py::test_professor_list_courses_sees_own tests/test_courses_router.py::test_get_course_detail_professor tests/test_courses_router.py::test_get_course_detail_wrong_professor_returns_404 -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/routers/courses.py tests/test_courses_router.py
git commit -m "feat: add course list and detail endpoints (GET /courses, GET /courses/{id})"
```

---

## Task 5: TDD — Course Update & Deactivate (`PUT /courses/{id}`, `DELETE /courses/{id}/deactivate`)

**Files:**
- Modify: `tests/test_courses_router.py`
- Modify: `app/routers/courses.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_courses_router.py`:

```python
async def test_professor_updates_course(client, professor):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Old Title", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]
    response = await client.put(
        f"/api/v1/courses/{course_id}",
        json={"title": "New Title", "semester": "Spring 2027"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "New Title"
    assert body["semester"] == "Spring 2027"


async def test_update_course_wrong_owner_returns_404(client, professor, rsa_keys):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Mine"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]

    import uuid
    from datetime import datetime, timezone
    from app.models.user import User, UserRole
    from tests.conftest import _TestSession, _make_token
    private_pem, _ = rsa_keys
    other = User(
        id=uuid.uuid4(),
        google_uid=f"other2-{uuid.uuid4().hex}",
        email=f"other2-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Other Prof",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    async with _TestSession() as s:
        s.add(other)
        await s.commit()
    other_token = _make_token(other.id, private_pem)

    response = await client.put(
        f"/api/v1/courses/{course_id}",
        json={"title": "Hijacked"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404


async def test_professor_deactivates_course(client, professor):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Active Course"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/v1/courses/{course_id}/deactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False


async def test_student_cannot_deactivate_course(client, student, professor):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Prof Course"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/v1/courses/{course_id}/deactivate",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/test_courses_router.py::test_professor_updates_course tests/test_courses_router.py::test_professor_deactivates_course -v
```

Expected: FAILED — 405 Method Not Allowed (handlers don't exist yet).

- [ ] **Step 3: Add `update_course` and `deactivate_course` to `app/routers/courses.py`**

Add after `get_course`:

```python
@router.put("/{course_id}", response_model=CourseOut)
async def update_course(
    course_id: uuid.UUID,
    body: CourseUpdate,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(course, field, value)

    await db.commit()
    await db.refresh(course)
    count = await _count_students(db, course.id)
    return _make_course_out(course, count)


@router.delete("/{course_id}/deactivate", response_model=CourseOut)
async def deactivate_course(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    course.is_active = False
    await db.commit()
    await db.refresh(course)
    count = await _count_students(db, course.id)
    return _make_course_out(course, count)
```

- [ ] **Step 4: Run all course tests**

```bash
uv run pytest tests/test_courses_router.py -v -k "not student_list_courses_sees_enrolled"
```

Expected: all pass except the skipped enrollment-dependent test.

- [ ] **Step 5: Commit**

```bash
git add app/routers/courses.py tests/test_courses_router.py
git commit -m "feat: add course update and deactivate endpoints"
```

---

## Task 6: TDD — Enrollment Join (`POST /enrollments/join`)

**Files:**
- Create: `tests/test_enrollments_router.py`
- Create: `app/routers/enrollments.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_enrollments_router.py`:

```python
import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")


async def test_student_joins_valid_course(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert create_resp.status_code == 201
    class_code = create_resp.json()["class_code"]

    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Psych 101"
    assert body["student_count"] == 1


async def test_student_joins_invalid_code_returns_404(client, student):
    _, stu_token = student
    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": "XXXXXX"},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 404


async def test_student_joins_already_enrolled_returns_409(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    class_code = create_resp.json()["class_code"]

    await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 409


async def test_student_joins_inactive_course_returns_410(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Closed Course"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]
    class_code = create_resp.json()["class_code"]

    await client.delete(
        f"/api/v1/courses/{course_id}/deactivate",
        headers={"Authorization": f"Bearer {prof_token}"},
    )

    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 410


async def test_professor_cannot_join_course(client, professor):
    _, prof_token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "My Course"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    class_code = create_resp.json()["class_code"]

    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/test_enrollments_router.py::test_student_joins_valid_course tests/test_enrollments_router.py::test_student_joins_invalid_code_returns_404 -v
```

Expected: FAILED — 404 (router not registered).

- [ ] **Step 3: Create `app/routers/enrollments.py`**

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.user import User
from app.routers.courses import _make_course_out
from app.schemas.course import CourseOut
from app.schemas.enrollment import EnrolledStudentOut, EnrollmentJoinRequest

router = APIRouter(tags=["enrollments"])


@router.post("/enrollments/join", response_model=CourseOut)
async def join_course(
    body: EnrollmentJoinRequest,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.class_code == body.class_code.upper())
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Invalid class code")
    if not course.is_active:
        raise HTTPException(status_code=410, detail="Course is no longer active")

    existing = await db.execute(
        select(Enrollment).where(
            Enrollment.user_id == current_user.id,
            Enrollment.course_id == course.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Already enrolled in this course")

    enrollment = Enrollment(user_id=current_user.id, course_id=course.id)
    db.add(enrollment)
    await db.commit()

    count_result = await db.execute(
        select(func.count()).select_from(Enrollment).where(Enrollment.course_id == course.id)
    )
    count = count_result.scalar_one()
    return _make_course_out(course, count)
```

- [ ] **Step 4: Register enrollments router in `app/main.py`**

```python
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import firebase_admin
import redis.asyncio as aioredis
from fastapi import FastAPI

from app.config import settings
from app.routers import auth, courses, enrollments, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not firebase_admin._apps and settings.firebase_project_id:
        firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="PocketPatient API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(courses.router, prefix="/api/v1")
app.include_router(enrollments.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
```

- [ ] **Step 5: Run all enrollment join tests**

```bash
uv run pytest tests/test_enrollments_router.py -k "join" -v
```

Expected: 5 passed.

- [ ] **Step 6: Now run the previously-skipped course test**

```bash
uv run pytest tests/test_courses_router.py::test_student_list_courses_sees_enrolled -v
```

Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add app/routers/enrollments.py app/main.py tests/test_enrollments_router.py
git commit -m "feat: add enrollment join endpoint (POST /enrollments/join)"
```

---

## Task 7: TDD — Student Management (`GET /courses/{id}/students`, `DELETE /courses/{id}/students/{user_id}`)

**Files:**
- Modify: `tests/test_enrollments_router.py`
- Modify: `app/routers/enrollments.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_enrollments_router.py`:

```python
async def test_professor_lists_enrolled_students(client, professor, student):
    prof_user, prof_token = professor
    stu_user, stu_token = student

    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]
    class_code = create_resp.json()["class_code"]

    await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )

    response = await client.get(
        f"/api/v1/courses/{course_id}/students",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert response.status_code == 200
    students = response.json()
    assert len(students) == 1
    assert students[0]["user_id"] == str(stu_user.id)
    assert "enrolled_at" in students[0]


async def test_list_students_wrong_professor_returns_404(client, professor, rsa_keys):
    _, prof_token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "My Course"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]

    import uuid
    from datetime import datetime, timezone
    from app.models.user import User, UserRole
    from tests.conftest import _TestSession, _make_token
    private_pem, _ = rsa_keys
    other = User(
        id=uuid.uuid4(),
        google_uid=f"other3-{uuid.uuid4().hex}",
        email=f"other3-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Other Prof",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    async with _TestSession() as s:
        s.add(other)
        await s.commit()
    other_token = _make_token(other.id, private_pem)

    response = await client.get(
        f"/api/v1/courses/{course_id}/students",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404


async def test_professor_removes_student(client, professor, student):
    prof_user, prof_token = professor
    stu_user, stu_token = student

    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]
    class_code = create_resp.json()["class_code"]

    await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )

    response = await client.delete(
        f"/api/v1/courses/{course_id}/students/{stu_user.id}",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert response.status_code == 204

    # Verify student is no longer listed
    students_resp = await client.get(
        f"/api/v1/courses/{course_id}/students",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert students_resp.json() == []


async def test_student_count_updates_after_enrollment(client, professor, student):
    _, prof_token = professor
    _, stu_token = student

    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Count Test"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert create_resp.json()["student_count"] == 0
    course_id = create_resp.json()["id"]
    class_code = create_resp.json()["class_code"]

    join_resp = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert join_resp.json()["student_count"] == 1

    detail_resp = await client.get(
        f"/api/v1/courses/{course_id}",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert detail_resp.json()["student_count"] == 1
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/test_enrollments_router.py::test_professor_lists_enrolled_students tests/test_enrollments_router.py::test_professor_removes_student -v
```

Expected: FAILED — 404 (handlers not implemented).

- [ ] **Step 3: Add `list_students` and `remove_student` to `app/routers/enrollments.py`**

Add after `join_course`:

```python
@router.get("/courses/{course_id}/students", response_model=list[EnrolledStudentOut])
async def list_students(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Course not found")

    rows = (
        await db.execute(
            select(User, Enrollment.enrolled_at)
            .join(Enrollment, Enrollment.user_id == User.id)
            .where(Enrollment.course_id == course_id)
        )
    ).all()

    return [
        EnrolledStudentOut(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            enrolled_at=enrolled_at,
        )
        for user, enrolled_at in rows
    ]


@router.delete("/courses/{course_id}/students/{user_id}", status_code=204)
async def remove_student(
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Course not found")

    enrollment_result = await db.execute(
        select(Enrollment).where(
            Enrollment.course_id == course_id,
            Enrollment.user_id == user_id,
        )
    )
    row = enrollment_result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Student not enrolled in this course")

    await db.delete(row)
    await db.commit()
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/test_courses_router.py tests/test_enrollments_router.py -v
```

Expected: all pass.

- [ ] **Step 5: Run old tests to confirm no regressions**

```bash
uv run pytest tests/test_auth_service.py tests/test_auth_router.py tests/test_users_router.py tests/test_deps.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/routers/enrollments.py tests/test_enrollments_router.py
git commit -m "feat: add student list and remove endpoints"
```

---

## Task 8: Update API Contract

**Files:**
- Modify: `docs/api-contract.md`

- [ ] **Step 1: Replace the Courses and add Enrollments section in `docs/api-contract.md`**

Replace everything from `## Courses` through the end of the file (before `## Health`) with:

```markdown
## Courses

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| POST | `/api/v1/courses` | Create a course (professor only) | Bearer JWT | ✅ Week 3 |
| GET | `/api/v1/courses` | List courses for current user | Bearer JWT | ✅ Week 3 |
| GET | `/api/v1/courses/{id}` | Get course details | Bearer JWT | ✅ Week 3 |
| PUT | `/api/v1/courses/{id}` | Update course (professor owner) | Bearer JWT | ✅ Week 3 |
| DELETE | `/api/v1/courses/{id}/deactivate` | Deactivate course (professor owner) | Bearer JWT | ✅ Week 3 |

### POST /api/v1/courses
**Role required:** professor  
**Request:** `{"title": "Psychiatry 101", "semester": "Fall 2026"}`  
**Response:** `CourseOut` — id, title, professor_id, class_code (6-char alphanumeric), semester, is_active, msg_window_start, msg_window_end, msg_timezone, created_at, student_count  
**Errors:** 401 unauthenticated, 403 not a professor

### GET /api/v1/courses
**Response:** array of `CourseOut`  
- Professors: courses they own  
- Students: courses they are enrolled in

### GET /api/v1/courses/{id}
**Response:** `CourseOut`  
**Errors:** 404 not found or user is not owner/enrolled (existence not leaked)

### PUT /api/v1/courses/{id}
**Role required:** professor (must own the course)  
**Request (all fields optional):** `{"title", "semester", "msg_window_start", "msg_window_end", "msg_timezone"}`  
**Response:** updated `CourseOut`  
**Errors:** 404 not found or not owner

### DELETE /api/v1/courses/{id}/deactivate
**Role required:** professor (must own the course)  
**Response:** updated `CourseOut` with `is_active: false`  
**Errors:** 404 not found or not owner

---

## Enrollments

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| POST | `/api/v1/enrollments/join` | Join a course via class code | Bearer JWT | ✅ Week 3 |
| GET | `/api/v1/courses/{id}/students` | List enrolled students (professor owner) | Bearer JWT | ✅ Week 3 |
| DELETE | `/api/v1/courses/{id}/students/{user_id}` | Remove a student (professor owner) | Bearer JWT | ✅ Week 3 |

### POST /api/v1/enrollments/join
**Role required:** student  
**Request:** `{"class_code": "ABC123"}`  
**Response:** `CourseOut` for the joined course (with updated student_count)  
**Errors:** 401 unauthenticated, 403 not a student, 404 invalid code, 409 already enrolled, 410 course inactive

### GET /api/v1/courses/{id}/students
**Role required:** professor (must own the course)  
**Response:** array of `{user_id, email, display_name, enrolled_at}`  
**Errors:** 404 not found or not owner

### DELETE /api/v1/courses/{id}/students/{user_id}
**Role required:** professor (must own the course)  
**Response:** 204 No Content  
**Errors:** 404 course not found, 404 student not enrolled

---
```

- [ ] **Step 2: Commit**

```bash
git add docs/api-contract.md
git commit -m "docs: update API contract with week 3 course and enrollment endpoints"
```

---

## Self-Review

**Spec coverage check:**

| Spec Requirement | Task |
|-----------------|------|
| `POST /courses` with class_code generation | Task 3 |
| `GET /courses` (role-filtered) | Task 4 |
| `GET /courses/{id}` | Task 4 |
| `PUT /courses/{id}` | Task 5 |
| `DELETE /courses/{id}/deactivate` | Task 5 |
| `POST /enrollments/join` with all error cases | Task 6 |
| `GET /courses/{id}/students` | Task 7 |
| `DELETE /courses/{id}/students/{user_id}` | Task 7 |
| Test DB setup in conftest | Task 2 |
| `student_count` on `CourseOut` | Tasks 1, 3 |
| Test: professor creates course → gets class_code | Task 3 |
| Test: student joins valid code → enrolled | Task 6 |
| Test: student joins invalid code → 404 | Task 6 |
| Test: student joins already-enrolled → 409 | Task 6 |
| Test: student tries to create course → 403 | Task 3 |
| Test: professor sees enrolled students | Task 7 |
| Update api-contract.md | Task 8 |

All spec requirements covered. ✅
