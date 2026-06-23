# Week 13 Student Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add student analytics endpoints — a cached summary (scores, trends, weak categories) and a paginated completed-sessions list — backed by SQL aggregations.

**Architecture:** SQL aggregation queries live in reusable helpers in `app/services/analytics_service.py`. A thin Redis JSON cache wrapper (`app/services/analytics_cache.py`) gives the summary a 5-min TTL with graceful degradation. A new `/analytics` router serves the summary; the existing sessions router gains a list endpoint and invalidates the summary cache on new scores.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Redis (`redis.asyncio`), pytest + pytest-asyncio, httpx `AsyncClient`.

## Global Constraints

- Python 3.11+, run everything via `uv` (e.g. `uv run pytest ...`).
- SQLAlchemy 2.0 async with `Mapped[]`; `from __future__ import annotations` at top of every module.
- Aggregations computed in SQL (`func.count`, `func.avg`, `.filter(...)`, `group_by`) — never loop in Python to compute averages/counts.
- Ownership checks return **404** (not 403) to avoid leaking existence; role failures return 403 via `require_role`.
- `total_score` is on a 0–100 scale. Weak-category threshold is avg **< 60**.
- `total_cases` = all sessions started (any status); `completed_cases` = `status == diagnosed`.
- Tests use the `clean_tables`, `professor`, `student` fixtures from `tests/conftest.py`; the test client sets `app.state.redis = AsyncMock()`.
- TDD: failing test → run (fail) → minimal impl → run (pass) → commit.

---

### Task 1: Redis JSON cache helper

**Files:**
- Create: `app/services/analytics_cache.py`
- Test: `tests/test_analytics_cache.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `summary_key(user_id: uuid.UUID, course_id: uuid.UUID) -> str`
  - `async get_cached_json(redis, key: str) -> dict | None`
  - `async set_cached_json(redis, key: str, value: dict, ttl: int = 300) -> None`
  - `async invalidate(redis, key: str) -> None`
  - constant `SUMMARY_TTL_SEC = 300`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analytics_cache.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analytics_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.analytics_cache'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/analytics_cache.py
from __future__ import annotations

import json
import uuid
from typing import Any

SUMMARY_TTL_SEC = 300


def summary_key(user_id: uuid.UUID, course_id: uuid.UUID) -> str:
    return f"analytics:summary:{user_id}:{course_id}"


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_analytics_cache.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/analytics_cache.py tests/test_analytics_cache.py
git commit -m "feat: redis JSON cache helper for analytics"
```

---

### Task 2: Analytics schemas

**Files:**
- Create: `app/schemas/analytics.py`

**Interfaces:**
- Consumes: nothing.
- Produces (Pydantic v2 models):
  - `ScoreByCase(session_id: uuid.UUID, disease_name: str, category: str, score: float | None, completed_at: datetime | None)`
  - `CategoryScore(avg_score: float, count: int)`
  - `ResponseTimePoint(case_number: int, avg_latency_sec: float | None)`
  - `StudentSummary(total_cases: int, completed_cases: int, avg_score: float | None, avg_response_time_sec: float | None, scores_by_case: list[ScoreByCase], scores_by_category: dict[str, CategoryScore], response_time_trend: list[ResponseTimePoint], weak_categories: list[str])`
  - `CompletedSessionItem(session_id: uuid.UUID, disease_name: str, category: str, score: float | None, turn_count: int, started_at: datetime, completed_at: datetime | None, avg_response_latency_sec: float | None)`
  - `PaginatedSessions(items: list[CompletedSessionItem], total: int, page: int, page_size: int)`

This task has no standalone test — these schemas are exercised by the endpoint
tests in Tasks 4 and 5. It is committed with Task 3 (the service that returns
`StudentSummary`). Create the file now so later tasks can import it.

- [ ] **Step 1: Create the schema module**

```python
# app/schemas/analytics.py
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ScoreByCase(BaseModel):
    session_id: uuid.UUID
    disease_name: str
    category: str
    score: float | None
    completed_at: datetime | None


class CategoryScore(BaseModel):
    avg_score: float
    count: int


class ResponseTimePoint(BaseModel):
    case_number: int
    avg_latency_sec: float | None


class StudentSummary(BaseModel):
    total_cases: int
    completed_cases: int
    avg_score: float | None
    avg_response_time_sec: float | None
    scores_by_case: list[ScoreByCase]
    scores_by_category: dict[str, CategoryScore]
    response_time_trend: list[ResponseTimePoint]
    weak_categories: list[str]


class CompletedSessionItem(BaseModel):
    session_id: uuid.UUID
    disease_name: str
    category: str
    score: float | None
    turn_count: int
    started_at: datetime
    completed_at: datetime | None
    avg_response_latency_sec: float | None


class PaginatedSessions(BaseModel):
    items: list[CompletedSessionItem]
    total: int
    page: int
    page_size: int
```

- [ ] **Step 2: Verify it imports**

Run: `uv run python -c "import app.schemas.analytics"`
Expected: no output, exit code 0

(No commit yet — committed in Task 3.)

---

### Task 3: `get_student_summary` service

**Files:**
- Modify: `app/services/analytics_service.py` (append new function + imports)
- Test: `tests/test_student_summary_service.py`

**Interfaces:**
- Consumes: `app.schemas.analytics.StudentSummary` and friends (Task 2).
- Produces: `async get_student_summary(user_id: uuid.UUID, course_id: uuid.UUID, db: AsyncSession) -> StudentSummary`
  - `scores_by_case` ordered by `completed_at` asc (then `started_at` asc).
  - `response_time_trend` `case_number` is the 1-based ordinal in that same ordering.
  - `weak_categories` = categories whose category-average `total_score` is `< 60`.
  - `avg_score` / category `avg_score` rounded to 1 decimal; `avg_response_time_sec` rounded to 1 decimal.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_student_summary_service.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from app.services.analytics_service import get_student_summary

pytestmark = pytest.mark.usefixtures("clean_tables")


async def _disease(db, unit_id, name, category):
    d = Disease(
        unit_id=unit_id, name=name, category=category,
        key_symptoms=["x"], differentials=["y"], difficulty_tier=2,
        speech_style="flat", nudge_behavior={},
    )
    db.add(d)
    await db.flush()
    return d


async def _completed(db, *, disease, user_id, course_id, score, latency, completed_at):
    s = Session(
        disease_id=disease.id, user_id=user_id, course_id=course_id,
        started_at=completed_at - timedelta(minutes=10), completed_at=completed_at,
        status=SessionStatus.diagnosed, turn_count=4, avg_response_latency_sec=latency,
    )
    db.add(s)
    await db.flush()
    db.add(Score(
        session_id=s.id, primary_dx=disease.name, differentials=[],
        justification="x" * 60, total_score=score,
    ))
    await db.flush()
    return s


@pytest_asyncio.fixture
async def summary_setup(db_session):
    prof = User(google_uid="su-prof", email="su-prof@test.edu", role=UserRole.professor, is_verified=True)
    stu = User(google_uid="su-stu", email="su-stu@test.edu", role=UserRole.student, is_verified=True)
    other = User(google_uid="su-other", email="su-other@test.edu", role=UserRole.student, is_verified=True)
    db_session.add_all([prof, stu, other])
    await db_session.flush()

    course = Course(title="Psych", professor_id=prof.id, class_code="SUM123")
    db_session.add(course)
    await db_session.flush()

    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()

    mdd = await _disease(db_session, unit.id, "MDD", "Mood")
    scz = await _disease(db_session, unit.id, "Schizophrenia", "Psychotic")

    base = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    # Two Mood cases (avg 80) and one Psychotic case (50 -> weak).
    await _completed(db_session, disease=mdd, user_id=stu.id, course_id=course.id,
                     score=90, latency=3600, completed_at=base)
    await _completed(db_session, disease=mdd, user_id=stu.id, course_id=course.id,
                     score=70, latency=2400, completed_at=base + timedelta(days=1))
    await _completed(db_session, disease=scz, user_id=stu.id, course_id=course.id,
                     score=50, latency=1800, completed_at=base + timedelta(days=2))
    # One active (incomplete) session -> counts toward total_cases only.
    active = Session(
        disease_id=mdd.id, user_id=stu.id, course_id=course.id,
        started_at=base + timedelta(days=3), status=SessionStatus.active, turn_count=1,
    )
    db_session.add(active)
    # Another student's completed case -> must NOT leak into stu's summary.
    await _completed(db_session, disease=mdd, user_id=other.id, course_id=course.id,
                     score=10, latency=9999, completed_at=base)
    await db_session.commit()
    return stu, course


async def test_summary_counts_and_averages(summary_setup, db_session):
    stu, course = summary_setup
    out = await get_student_summary(stu.id, course.id, db_session)
    assert out.total_cases == 4          # 3 diagnosed + 1 active
    assert out.completed_cases == 3
    assert out.avg_score == pytest.approx(70.0)   # (90+70+50)/3
    assert out.avg_response_time_sec == pytest.approx(2600.0)  # (3600+2400+1800)/3


async def test_summary_scores_by_case_ordered(summary_setup, db_session):
    stu, course = summary_setup
    out = await get_student_summary(stu.id, course.id, db_session)
    assert [c.score for c in out.scores_by_case] == [90, 70, 50]
    assert out.scores_by_case[0].disease_name == "MDD"
    assert out.scores_by_case[2].category == "Psychotic"


async def test_summary_category_and_weak(summary_setup, db_session):
    stu, course = summary_setup
    out = await get_student_summary(stu.id, course.id, db_session)
    assert out.scores_by_category["Mood"].avg_score == pytest.approx(80.0)
    assert out.scores_by_category["Mood"].count == 2
    assert out.scores_by_category["Psychotic"].avg_score == pytest.approx(50.0)
    assert out.weak_categories == ["Psychotic"]


async def test_summary_response_time_trend(summary_setup, db_session):
    stu, course = summary_setup
    out = await get_student_summary(stu.id, course.id, db_session)
    assert [p.case_number for p in out.response_time_trend] == [1, 2, 3]
    assert [p.avg_latency_sec for p in out.response_time_trend] == [3600, 2400, 1800]


async def test_summary_empty(db_session, summary_setup):
    stu, course = summary_setup
    out = await get_student_summary(uuid.uuid4(), course.id, db_session)
    assert out.total_cases == 0
    assert out.completed_cases == 0
    assert out.avg_score is None
    assert out.avg_response_time_sec is None
    assert out.scores_by_case == []
    assert out.scores_by_category == {}
    assert out.response_time_trend == []
    assert out.weak_categories == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_student_summary_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_student_summary'`

- [ ] **Step 3: Write minimal implementation**

Add these imports near the top of `app/services/analytics_service.py` (it already imports `select`, `AsyncSession`, `Disease`, `Session`):

```python
from sqlalchemy import func, select

from app.models.score import Score
from app.models.session import SessionStatus
from app.schemas.analytics import (
    CategoryScore,
    ResponseTimePoint,
    ScoreByCase,
    StudentSummary,
)
```

Append this function to `app/services/analytics_service.py`:

```python
WEAK_CATEGORY_THRESHOLD = 60.0


async def get_student_summary(
    user_id: uuid.UUID, course_id: uuid.UUID, db: AsyncSession
) -> StudentSummary:
    scope = (Session.user_id == user_id, Session.course_id == course_id)

    # Query 1 — counts + overall averages (SQL aggregation, single row).
    counts_row = (
        await db.execute(
            select(
                func.count(Session.id).label("total"),
                func.count(Session.id)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("completed"),
                func.avg(Score.total_score).label("avg_score"),
                func.avg(Session.avg_response_latency_sec)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("avg_rt"),
            )
            .select_from(Session)
            .outerjoin(Score, Score.session_id == Session.id)
            .where(*scope)
        )
    ).one()

    # Query 2 — per-case rows (diagnosed), ordered by completion.
    case_rows = (
        await db.execute(
            select(
                Session.id,
                Disease.name,
                Disease.category,
                Score.total_score,
                Session.completed_at,
                Session.avg_response_latency_sec,
            )
            .join(Disease, Disease.id == Session.disease_id)
            .join(Score, Score.session_id == Session.id)
            .where(*scope, Session.status == SessionStatus.diagnosed)
            .order_by(
                Session.completed_at.asc().nulls_last(), Session.started_at.asc()
            )
        )
    ).all()

    # Query 3 — per-category aggregation.
    cat_rows = (
        await db.execute(
            select(
                Disease.category,
                func.avg(Score.total_score).label("avg_score"),
                func.count(Score.id).label("count"),
            )
            .select_from(Session)
            .join(Disease, Disease.id == Session.disease_id)
            .join(Score, Score.session_id == Session.id)
            .where(*scope, Session.status == SessionStatus.diagnosed)
            .group_by(Disease.category)
        )
    ).all()

    scores_by_case = [
        ScoreByCase(
            session_id=r.id,
            disease_name=r.name,
            category=r.category,
            score=r.total_score,
            completed_at=r.completed_at,
        )
        for r in case_rows
    ]
    response_time_trend = [
        ResponseTimePoint(case_number=i, avg_latency_sec=r.avg_response_latency_sec)
        for i, r in enumerate(case_rows, start=1)
    ]
    scores_by_category = {
        r.category: CategoryScore(avg_score=round(r.avg_score, 1), count=r.count)
        for r in cat_rows
    }
    weak_categories = [
        r.category for r in cat_rows if r.avg_score < WEAK_CATEGORY_THRESHOLD
    ]

    return StudentSummary(
        total_cases=counts_row.total,
        completed_cases=counts_row.completed,
        avg_score=round(counts_row.avg_score, 1) if counts_row.avg_score is not None else None,
        avg_response_time_sec=round(counts_row.avg_rt, 1) if counts_row.avg_rt is not None else None,
        scores_by_case=scores_by_case,
        scores_by_category=scores_by_category,
        response_time_trend=response_time_trend,
        weak_categories=weak_categories,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_student_summary_service.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/schemas/analytics.py app/services/analytics_service.py tests/test_student_summary_service.py
git commit -m "feat: get_student_summary SQL aggregation service + analytics schemas"
```

---

### Task 4: Student summary endpoint + router registration

**Files:**
- Create: `app/routers/analytics.py`
- Modify: `app/main.py` (import + register router)
- Test: `tests/test_analytics_router.py`

**Interfaces:**
- Consumes: `get_student_summary` (Task 3); `summary_key`, `get_cached_json`, `set_cached_json` (Task 1); `require_role` (`app/deps.py`); `get_db` (`app/database.py`).
- Produces: `GET /api/v1/analytics/student/summary?course_id={uuid}` → `StudentSummary`. Student-only (403 for professors).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analytics_router.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus

pytestmark = pytest.mark.usefixtures("clean_tables")


@pytest_asyncio.fixture
async def seeded_course(db_session, professor, student):
    prof, _ = professor
    stu, _ = student
    course = Course(title="Psych", professor_id=prof.id, class_code="ARS123")
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(
        unit_id=unit.id, name="MDD", category="Mood", key_symptoms=["x"],
        differentials=["y"], difficulty_tier=2, speech_style="flat", nudge_behavior={},
    )
    db_session.add(disease)
    await db_session.flush()
    completed_at = datetime(2026, 8, 15, tzinfo=timezone.utc)
    sess = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=completed_at - timedelta(minutes=10), completed_at=completed_at,
        status=SessionStatus.diagnosed, turn_count=3, avg_response_latency_sec=1200,
    )
    db_session.add(sess)
    await db_session.flush()
    db_session.add(Score(session_id=sess.id, primary_dx="MDD", differentials=[],
                         justification="x" * 60, total_score=85))
    await db_session.commit()
    return course


async def test_summary_endpoint_returns_data(client, student, seeded_course):
    _, token = student
    resp = await client.get(
        f"/api/v1/analytics/student/summary?course_id={seeded_course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_cases"] == 1
    assert body["completed_cases"] == 1
    assert body["avg_score"] == 85
    assert body["scores_by_case"][0]["disease_name"] == "MDD"
    assert body["scores_by_category"]["Mood"]["count"] == 1


async def test_summary_endpoint_forbidden_for_professor(client, professor, seeded_course):
    _, token = professor
    resp = await client.get(
        f"/api/v1/analytics/student/summary?course_id={seeded_course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_summary_endpoint_requires_course_id(client, student):
    _, token = student
    resp = await client.get(
        "/api/v1/analytics/student/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_summary_endpoint_empty(client, student):
    _, token = student
    resp = await client.get(
        f"/api/v1/analytics/student/summary?course_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total_cases"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analytics_router.py -v`
Expected: FAIL — all four return 404 (route not registered yet).

- [ ] **Step 3: Write minimal implementation**

```python
# app/routers/analytics.py
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models.user import User
from app.schemas.analytics import StudentSummary
from app.services.analytics_cache import (
    get_cached_json,
    set_cached_json,
    summary_key,
)
from app.services.analytics_service import get_student_summary

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/student/summary", response_model=StudentSummary)
async def student_summary(
    course_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
) -> StudentSummary:
    redis = getattr(request.app.state, "redis", None)
    key = summary_key(current_user.id, course_id)

    cached = await get_cached_json(redis, key)
    if cached is not None:
        return StudentSummary.model_validate(cached)

    summary = await get_student_summary(current_user.id, course_id, db)
    await set_cached_json(redis, key, summary.model_dump(mode="json"))
    return summary
```

In `app/main.py`, add `analytics` to the routers import line and register it.
Change the import:

```python
from app.routers import analytics, auth, courses, disease_documents, enrollments, sessions, units, users
```

Add after the existing `app.include_router(sessions.router, ...)` line:

```python
app.include_router(analytics.router, prefix="/api/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_analytics_router.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/routers/analytics.py app/main.py tests/test_analytics_router.py
git commit -m "feat: GET /analytics/student/summary endpoint with redis cache"
```

---

### Task 5: Completed-sessions list endpoint

**Files:**
- Modify: `app/services/analytics_service.py` (add `list_completed_sessions`)
- Modify: `app/routers/sessions.py` (add `GET ""` list route + imports)
- Test: `tests/test_sessions_list.py`

**Interfaces:**
- Consumes: `CompletedSessionItem`, `PaginatedSessions` (Task 2); `get_current_user`, `UserRole`, `Course`, `Session`, `Disease`, `Score`, `SessionStatus`.
- Produces:
  - Service: `async list_completed_sessions(db, *, course_id, user_id=None, status=None, page=1, page_size=20) -> tuple[list[CompletedSessionItem], int]` — returns `(items, total)`, ordered by `completed_at` desc nulls last then `started_at` desc.
  - Route: `GET /api/v1/sessions?course_id={uuid}&status=&student_id=&page=1&page_size=20` → `PaginatedSessions`. Students see only their own (ignoring `student_id`); professors must own the course (404 otherwise) and may filter by `student_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sessions_list.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole

pytestmark = pytest.mark.usefixtures("clean_tables")


async def _make_completed(db, *, disease_id, user_id, course_id, score, completed_at):
    s = Session(
        disease_id=disease_id, user_id=user_id, course_id=course_id,
        started_at=completed_at - timedelta(minutes=10), completed_at=completed_at,
        status=SessionStatus.diagnosed, turn_count=3, avg_response_latency_sec=600,
    )
    db.add(s)
    await db.flush()
    db.add(Score(session_id=s.id, primary_dx="MDD", differentials=[],
                 justification="x" * 60, total_score=score))
    await db.flush()
    return s


@pytest_asyncio.fixture
async def list_setup(db_session, professor, student):
    prof, _ = professor
    stu, _ = student
    other = User(google_uid="ls-other", email="ls-other@test.edu",
                 role=UserRole.student, is_verified=True)
    db_session.add(other)
    await db_session.flush()

    course = Course(title="Psych", professor_id=prof.id, class_code="LST123")
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(unit_id=unit.id, name="MDD", category="Mood", key_symptoms=["x"],
                      differentials=["y"], difficulty_tier=2, speech_style="flat",
                      nudge_behavior={})
    db_session.add(disease)
    await db_session.flush()

    base = datetime(2026, 8, 10, tzinfo=timezone.utc)
    for i in range(3):
        await _make_completed(db_session, disease_id=disease.id, user_id=stu.id,
                              course_id=course.id, score=80 + i,
                              completed_at=base + timedelta(days=i))
    # An active session (not diagnosed) for the student.
    db_session.add(Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                           started_at=base + timedelta(days=9), status=SessionStatus.active,
                           turn_count=1))
    # Another student's completed session in the same course.
    await _make_completed(db_session, disease_id=disease.id, user_id=other.id,
                          course_id=course.id, score=10, completed_at=base)
    await db_session.commit()
    return course, stu, other


async def test_student_sees_only_own_diagnosed(client, student, list_setup):
    course, _, _ = list_setup
    _, token = student
    resp = await client.get(
        f"/api/v1/sessions?course_id={course.id}&status=diagnosed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # Ordered by completed_at desc -> highest score (latest) first.
    assert body["items"][0]["score"] == 82
    assert all(it["disease_name"] == "MDD" for it in body["items"])


async def test_pagination(client, student, list_setup):
    course, _, _ = list_setup
    _, token = student
    resp = await client.get(
        f"/api/v1/sessions?course_id={course.id}&status=diagnosed&page=2&page_size=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert len(body["items"]) == 1


async def test_professor_must_own_course(client, professor, student, db_session, list_setup):
    # professor fixture owns the course; a different course id they don't own -> 404
    course, _, _ = list_setup
    _, token = professor
    resp = await client.get(
        f"/api/v1/sessions?course_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_professor_filters_by_student(client, professor, list_setup):
    course, stu, other = list_setup
    _, token = professor
    resp = await client.get(
        f"/api/v1/sessions?course_id={course.id}&status=diagnosed&student_id={other.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["score"] == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sessions_list.py -v`
Expected: FAIL — list route resolves against `GET /sessions/{session_id}` or 404; assertions fail.

- [ ] **Step 3: Write minimal implementation**

Add to `app/services/analytics_service.py` (imports `Score`, `SessionStatus`, `func`, `select` already added in Task 3; also import the schemas):

```python
from app.schemas.analytics import CompletedSessionItem  # add to the analytics-schema import group
```

Append:

```python
async def list_completed_sessions(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    status: SessionStatus | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CompletedSessionItem], int]:
    filters = [Session.course_id == course_id]
    if user_id is not None:
        filters.append(Session.user_id == user_id)
    if status is not None:
        filters.append(Session.status == status)

    total = (
        await db.execute(
            select(func.count(Session.id)).where(*filters)
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(
                Session.id,
                Disease.name,
                Disease.category,
                Score.total_score,
                Session.turn_count,
                Session.started_at,
                Session.completed_at,
                Session.avg_response_latency_sec,
            )
            .join(Disease, Disease.id == Session.disease_id)
            .outerjoin(Score, Score.session_id == Session.id)
            .where(*filters)
            .order_by(
                Session.completed_at.desc().nulls_last(), Session.started_at.desc()
            )
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()

    items = [
        CompletedSessionItem(
            session_id=r.id,
            disease_name=r.name,
            category=r.category,
            score=r.total_score,
            turn_count=r.turn_count,
            started_at=r.started_at,
            completed_at=r.completed_at,
            avg_response_latency_sec=r.avg_response_latency_sec,
        )
        for r in rows
    ]
    return items, total
```

In `app/routers/sessions.py`, add imports:

```python
from fastapi import Query  # add to existing fastapi import
from app.schemas.analytics import PaginatedSessions
from app.services.analytics_service import list_completed_sessions
```

Add this route **above** the `@router.get("/{session_id}", ...)` definition so the
empty path is matched explicitly first:

```python
@router.get("", response_model=PaginatedSessions)
async def list_sessions(
    course_id: uuid.UUID,
    status: SessionStatus | None = None,
    student_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedSessions:
    if current_user.role == UserRole.student:
        user_filter: uuid.UUID | None = current_user.id
    else:
        course = (
            await db.execute(
                select(Course).where(
                    Course.id == course_id,
                    Course.professor_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if course is None:
            raise HTTPException(status_code=404, detail="Course not found")
        user_filter = student_id

    items, total = await list_completed_sessions(
        db,
        course_id=course_id,
        user_id=user_filter,
        status=status,
        page=page,
        page_size=page_size,
    )
    return PaginatedSessions(items=items, total=total, page=page, page_size=page_size)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sessions_list.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/analytics_service.py app/routers/sessions.py tests/test_sessions_list.py
git commit -m "feat: GET /sessions paginated completed-sessions list"
```

---

### Task 6: Invalidate summary cache on new score

**Files:**
- Modify: `app/routers/sessions.py` (`diagnose` endpoint)
- Test: `tests/test_summary_cache_invalidation.py`

**Interfaces:**
- Consumes: `summary_key`, `invalidate` (Task 1).
- Produces: after a Score is committed in `diagnose`, the summary cache key for `(user_id, course_id)` is deleted.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_summary_cache_invalidation.py
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.services.analytics_cache import summary_key

pytestmark = pytest.mark.usefixtures("clean_tables")


@pytest_asyncio.fixture
async def diagnose_setup(db_session, professor, student, monkeypatch):
    prof, _ = professor
    stu, _ = student
    course = Course(title="Psych", professor_id=prof.id, class_code="INV123")
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(unit_id=unit.id, name="MDD", category="Mood", key_symptoms=["x"],
                      differentials=["y"], difficulty_tier=2, speech_style="flat",
                      nudge_behavior={})
    db_session.add(disease)
    await db_session.flush()
    sess = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                   started_at=datetime.now(timezone.utc), status=SessionStatus.active,
                   turn_count=2)
    db_session.add(sess)
    await db_session.commit()

    # Make grading deterministic + correct so a Score is committed.
    async def fake_grade(session, submission, db):
        from app.models.score import Score
        session.avg_response_latency_sec = 100.0
        return Score(session_id=session.id, primary_dx=submission.primary_dx,
                     differentials=[], justification=submission.justification,
                     is_correct=True, rubric_score=90.0, response_time_score=100.0,
                     total_score=92.0, feedback_text="ok",
                     graded_at=datetime.now(timezone.utc))

    monkeypatch.setattr("app.routers.sessions.grade_diagnosis", fake_grade)
    return course, stu, sess


async def test_diagnose_invalidates_summary_cache(client, student, diagnose_setup):
    from app.main import app as fastapi_app

    course, stu, sess = diagnose_setup
    _, token = student
    redis = AsyncMock()
    fastapi_app.state.redis = redis  # same global app the test client wraps

    resp = await client.post(
        f"/api/v1/sessions/{sess.id}/diagnose",
        headers={"Authorization": f"Bearer {token}"},
        json={"primary_dx": "MDD", "differentials": [], "justification": "x" * 60},
    )
    assert resp.status_code == 200
    assert resp.json()["correct"] is True
    redis.delete.assert_awaited_with(summary_key(stu.id, course.id))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_summary_cache_invalidation.py -v`
Expected: FAIL — `redis.delete` was never awaited (no invalidation yet).

- [ ] **Step 3: Write minimal implementation**

In `app/routers/sessions.py`, add imports:

```python
from fastapi import Request  # add to existing fastapi import
from app.services.analytics_cache import invalidate, summary_key
```

Change the `diagnose` signature to accept the request:

```python
@router.post("/{session_id}/diagnose", response_model=DiagnosisResult)
async def diagnose(
    session_id: uuid.UUID,
    body: DiagnosisCreate,
    request: Request,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
) -> DiagnosisResult:
```

In the correct-diagnosis branch, after `await db.refresh(score)` and before
loading the unit, invalidate the cache:

```python
    redis = getattr(request.app.state, "redis", None)
    await invalidate(redis, summary_key(current_user.id, session.course_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_summary_cache_invalidation.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the full suite + commit**

Run: `uv run pytest -q`
Expected: PASS (no regressions — existing `tests/test_sessions_router.py` `diagnose` tests still pass with the new `request` param).

```bash
git add app/routers/sessions.py tests/test_summary_cache_invalidation.py
git commit -m "feat: invalidate student summary cache on new score"
```

---

## Notes for the implementer

- `func.count(...).filter(...)` emits Postgres `count(*) FILTER (WHERE ...)` — supported by the test/dev Postgres; this is how `total_cases` vs `completed_cases` come from one query.
- `.nulls_last()` is required because active/abandoned sessions have `completed_at IS NULL`; without it Postgres sorts NULLs first under `DESC`.
- In Task 6's test, `from app.main import app` is the same global FastAPI app the conftest `client` fixture wraps via `ASGITransport(app=app)`; reassigning `app.state.redis` swaps the conftest `AsyncMock` for the test's own so `assert_awaited_with` can inspect it.
- Do not register the `/analytics` router twice; it is added once in `app/main.py` (Task 4).
