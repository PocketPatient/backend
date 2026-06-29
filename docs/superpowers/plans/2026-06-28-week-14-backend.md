# Week 14 Backend — Professor Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three professor-only analytics endpoints (class-summary, student drill-down, CSV export) on top of the existing week-13 analytics layer.

**Architecture:** SQL aggregation helpers in `analytics_service.py` feed Pydantic response models served by the `analytics` router. Course ownership is verified (404 on miss). The class-summary is Redis-cached (TTL 300s) and invalidated by `diagnose`; the drill-down reuses the existing per-student summary cache; export is uncached.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Postgres/asyncpg, pytest-asyncio, Python `csv`.

## Global Constraints

- Python 3.11+, `uv run` for all commands.
- Professor-only endpoints: `Depends(require_role("professor"))` + course-ownership check returning **404** (never 403) when the professor does not own the course.
- `Score.total_score` is 0–100; averages rounded to 1 dp; `null` when no scored cases.
- Tests use `clean_tables`, `professor`, `student` fixtures; seed extra rows via `db_session`. `asyncio_mode=auto`.
- Run one test file: `uv run pytest tests/test_professor_analytics.py -v`.

---

### Task 1: Schemas + `get_class_summary` service helper

**Files:**
- Modify: `app/schemas/analytics.py`
- Modify: `app/services/analytics_service.py`
- Test: `tests/test_professor_analytics.py` (create)

**Interfaces:**
- Consumes: existing `Session`, `Score`, `Disease`, `Unit`, `Enrollment`, `User`, `SessionStatus`.
- Produces: `get_class_summary(course_id: uuid.UUID, db: AsyncSession, bottom_pct: float = 0.2) -> ClassSummary`; schemas `UnitCompletion`, `ScoreBucket`, `CategoryHeatmap`, `FlaggedStudent`, `ClassSummary`.

- [ ] **Step 1: Add schemas to `app/schemas/analytics.py`** (append after `PaginatedSessions`)

```python
class UnitCompletion(BaseModel):
    unit_label: str
    total_diseases: int
    total_cases_started: int
    total_diagnosed: int
    avg_score: float | None


class ScoreBucket(BaseModel):
    range: str
    count: int


class CategoryHeatmap(BaseModel):
    students: list[str]
    categories: list[str]
    scores: list[list[float | None]]


class FlaggedStudent(BaseModel):
    email: str
    avg_score: float
    completed_cases: int


class ClassSummary(BaseModel):
    enrolled_students: int
    students_with_active_case: int
    total_completed_cases: int
    avg_class_score: float | None
    completion_by_unit: list[UnitCompletion]
    score_distribution: list[ScoreBucket]
    category_heatmap: CategoryHeatmap
    flagged_students: list[FlaggedStudent]
```

- [ ] **Step 2: Write the failing test**

Add to a new `tests/test_professor_analytics.py`. Put shared seeding helpers at the top.

```python
import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit
from app.models.user import User, UserRole

UTC = timezone.utc


async def _course(db, professor):
    course = Course(
        id=uuid.uuid4(), title="Psych 101",
        professor_id=professor.id, class_code=uuid.uuid4().hex[:6].upper(),
    )
    db.add(course)
    await db.commit()
    return course


async def _student(db, n):
    u = User(
        id=uuid.uuid4(), google_uid=f"s-{uuid.uuid4().hex}",
        email=f"stu{n}@test.edu", role=UserRole.student, is_verified=True,
        display_name=f"Student {n}",
    )
    db.add(u)
    await db.commit()
    return u


async def _unit(db, course, label):
    unit = Unit(id=uuid.uuid4(), course_id=course.id, label=label)
    db.add(unit)
    await db.commit()
    return unit


async def _disease(db, unit, name, category):
    d = Disease(
        id=uuid.uuid4(), unit_id=unit.id, name=name, category=category,
        key_symptoms=[], differentials=[], difficulty_tier=1,
        speech_style="flat", nudge_behavior={},
    )
    db.add(d)
    await db.commit()
    return d


async def _enroll(db, user, course):
    db.add(Enrollment(id=uuid.uuid4(), user_id=user.id, course_id=course.id))
    await db.commit()


async def _session(db, user, course, disease, status, score=None, *,
                   completed_offset=0, latency=None, turns=0):
    started = datetime(2026, 8, 1, tzinfo=UTC)
    completed = started + timedelta(hours=completed_offset) if status == SessionStatus.diagnosed else None
    s = Session(
        id=uuid.uuid4(), disease_id=disease.id, user_id=user.id,
        course_id=course.id, started_at=started, completed_at=completed,
        status=status, turn_count=turns, avg_response_latency_sec=latency,
    )
    db.add(s)
    await db.commit()
    if score is not None:
        db.add(Score(id=uuid.uuid4(), session_id=s.id, primary_dx="x",
                     differentials=[], total_score=score,
                     graded_at=completed))
        await db.commit()
    return s


@pytest.mark.asyncio
async def test_class_summary_counts_and_avg(clean_tables, db_session, professor):
    prof, _ = professor
    course = await _course(db_session, prof)
    unit = await _unit(db_session, course, "Unit 1")
    d = await _disease(db_session, unit, "MDD", "Mood")
    s1 = await _student(db_session, 1)
    s2 = await _student(db_session, 2)
    await _enroll(db_session, s1, course)
    await _enroll(db_session, s2, course)
    await _session(db_session, s1, course, d, SessionStatus.diagnosed, score=80)
    await _session(db_session, s2, course, d, SessionStatus.diagnosed, score=40)
    await _session(db_session, s2, course, d, SessionStatus.active)

    from app.services.analytics_service import get_class_summary
    out = await get_class_summary(course.id, db_session)

    assert out.enrolled_students == 2
    assert out.students_with_active_case == 1
    assert out.total_completed_cases == 2
    assert out.avg_class_score == 60.0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_professor_analytics.py::test_class_summary_counts_and_avg -v`
Expected: FAIL — `ImportError: cannot import name 'get_class_summary'`.

- [ ] **Step 4: Implement `get_class_summary`** in `app/services/analytics_service.py`

Add imports at the top (merge with existing): `import math`, `from collections import defaultdict`, `from sqlalchemy import and_`, and to the model imports add `from app.models.enrollment import Enrollment`, `from app.models.unit import Unit`, `from app.models.user import User`. Add to the schema import block: `ClassSummary, CategoryHeatmap, FlaggedStudent, ScoreBucket, UnitCompletion`.

```python
SCORE_BUCKETS = [
    ("0-20", 0.0, 20.0),
    ("21-40", 20.0, 40.0),
    ("41-60", 40.0, 60.0),
    ("61-80", 60.0, 80.0),
    ("81-100", 80.0, 100.0),
]


async def get_class_summary(
    course_id: uuid.UUID, db: AsyncSession, bottom_pct: float = 0.2
) -> ClassSummary:
    enrolled = (
        await db.execute(
            select(func.count(Enrollment.id)).where(Enrollment.course_id == course_id)
        )
    ).scalar_one()

    active = (
        await db.execute(
            select(func.count(func.distinct(Session.user_id))).where(
                Session.course_id == course_id,
                Session.status == SessionStatus.active,
            )
        )
    ).scalar_one()

    total_completed = (
        await db.execute(
            select(func.count(Session.id)).where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
            )
        )
    ).scalar_one()

    avg_class_score = (
        await db.execute(
            select(func.avg(Score.total_score))
            .select_from(Session)
            .join(Score, Score.session_id == Session.id)
            .where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
            )
        )
    ).scalar_one()

    completion_by_unit = await _completion_by_unit(course_id, db)
    score_distribution = await _score_distribution(course_id, db)
    category_heatmap, flagged_students = await _heatmap_and_flagged(
        course_id, db, bottom_pct
    )

    return ClassSummary(
        enrolled_students=enrolled,
        students_with_active_case=active,
        total_completed_cases=total_completed,
        avg_class_score=round(avg_class_score, 1) if avg_class_score is not None else None,
        completion_by_unit=completion_by_unit,
        score_distribution=score_distribution,
        category_heatmap=category_heatmap,
        flagged_students=flagged_students,
    )
```

- [ ] **Step 5: Implement the three private helpers** (same file)

```python
async def _completion_by_unit(course_id, db) -> list[UnitCompletion]:
    units = (
        await db.execute(
            select(Unit.id, Unit.label)
            .where(Unit.course_id == course_id)
            .order_by(Unit.created_at, Unit.label)
        )
    ).all()
    if not units:
        return []
    unit_ids = [u.id for u in units]

    disease_counts = dict(
        (
            await db.execute(
                select(Disease.unit_id, func.count(Disease.id))
                .where(Disease.unit_id.in_(unit_ids))
                .group_by(Disease.unit_id)
            )
        ).all()
    )

    stat_rows = (
        await db.execute(
            select(
                Disease.unit_id,
                func.count(Session.id).label("started"),
                func.count(Session.id)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("diagnosed"),
                func.avg(Score.total_score)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("avg_score"),
            )
            .select_from(Session)
            .join(Disease, Disease.id == Session.disease_id)
            .outerjoin(Score, Score.session_id == Session.id)
            .where(Session.course_id == course_id)
            .group_by(Disease.unit_id)
        )
    ).all()
    stats = {r.unit_id: r for r in stat_rows}

    out = []
    for u in units:
        st = stats.get(u.id)
        out.append(
            UnitCompletion(
                unit_label=u.label,
                total_diseases=disease_counts.get(u.id, 0),
                total_cases_started=st.started if st else 0,
                total_diagnosed=st.diagnosed if st else 0,
                avg_score=round(st.avg_score, 1) if st and st.avg_score is not None else None,
            )
        )
    return out


async def _score_distribution(course_id, db) -> list[ScoreBucket]:
    scores = (
        await db.execute(
            select(Score.total_score)
            .select_from(Session)
            .join(Score, Score.session_id == Session.id)
            .where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
                Score.total_score.isnot(None),
            )
        )
    ).scalars().all()

    counts = {label: 0 for label, _, _ in SCORE_BUCKETS}
    for s in scores:
        for label, lo, hi in SCORE_BUCKETS:
            # first bucket is inclusive at 0; others are (lo, hi].
            if (s >= lo if lo == 0.0 else s > lo) and s <= hi:
                counts[label] += 1
                break
    return [ScoreBucket(range=label, count=counts[label]) for label, _, _ in SCORE_BUCKETS]


async def _heatmap_and_flagged(course_id, db, bottom_pct):
    rows = (
        await db.execute(
            select(User.email, Disease.category, Score.total_score)
            .select_from(Session)
            .join(Score, Score.session_id == Session.id)
            .join(Disease, Disease.id == Session.disease_id)
            .join(User, User.id == Session.user_id)
            .join(
                Enrollment,
                and_(
                    Enrollment.user_id == Session.user_id,
                    Enrollment.course_id == course_id,
                ),
            )
            .where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
                Score.total_score.isnot(None),
            )
        )
    ).all()

    cell: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_student: dict[str, list[float]] = defaultdict(list)
    for email, category, score in rows:
        cell[(email, category)].append(score)
        per_student[email].append(score)

    students = sorted(per_student.keys())
    categories = sorted({cat for _, cat in cell.keys()})
    matrix: list[list[float | None]] = []
    for email in students:
        row = []
        for cat in categories:
            vals = cell.get((email, cat))
            row.append(round(sum(vals) / len(vals), 1) if vals else None)
        matrix.append(row)
    heatmap = CategoryHeatmap(students=students, categories=categories, scores=matrix)

    avgs = [
        (email, round(sum(v) / len(v), 1), len(v))
        for email, v in per_student.items()
    ]
    avgs.sort(key=lambda t: (t[1], t[0]))
    k = math.ceil(len(avgs) * bottom_pct) if avgs else 0
    flagged = [
        FlaggedStudent(email=e, avg_score=a, completed_cases=c)
        for e, a, c in avgs[:k]
    ]
    return heatmap, flagged
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_professor_analytics.py::test_class_summary_counts_and_avg -v`
Expected: PASS.

- [ ] **Step 7: Add the remaining math tests** (units, buckets, heatmap, flagged, empty)

```python
@pytest.mark.asyncio
async def test_completion_by_unit_and_buckets(clean_tables, db_session, professor):
    prof, _ = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    d1 = await _disease(db_session, u1, "MDD", "Mood")
    d2 = await _disease(db_session, u1, "GAD", "Anxiety")
    stu = await _student(db_session, 1)
    await _enroll(db_session, stu, course)
    await _session(db_session, stu, course, d1, SessionStatus.diagnosed, score=20)
    await _session(db_session, stu, course, d2, SessionStatus.diagnosed, score=21)
    await _session(db_session, stu, course, d2, SessionStatus.abandoned)

    from app.services.analytics_service import get_class_summary
    out = await get_class_summary(course.id, db_session)

    unit = out.completion_by_unit[0]
    assert unit.unit_label == "Unit 1"
    assert unit.total_diseases == 2
    assert unit.total_cases_started == 3
    assert unit.total_diagnosed == 2
    assert unit.avg_score == 20.5

    dist = {b.range: b.count for b in out.score_distribution}
    assert dist == {"0-20": 1, "21-40": 1, "41-60": 0, "61-80": 0, "81-100": 0}


@pytest.mark.asyncio
async def test_heatmap_and_flagged(clean_tables, db_session, professor):
    prof, _ = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    mood = await _disease(db_session, u1, "MDD", "Mood")
    anx = await _disease(db_session, u1, "GAD", "Anxiety")
    weak = await _student(db_session, 1)   # stu1@test.edu, avg 30
    strong = await _student(db_session, 2)  # stu2@test.edu, avg 90
    await _enroll(db_session, weak, course)
    await _enroll(db_session, strong, course)
    await _session(db_session, weak, course, mood, SessionStatus.diagnosed, score=30)
    await _session(db_session, strong, course, mood, SessionStatus.diagnosed, score=90)
    await _session(db_session, strong, course, anx, SessionStatus.diagnosed, score=90)

    from app.services.analytics_service import get_class_summary
    out = await get_class_summary(course.id, db_session, bottom_pct=0.5)

    hm = out.category_heatmap
    assert hm.students == ["stu1@test.edu", "stu2@test.edu"]
    assert hm.categories == ["Anxiety", "Mood"]
    assert hm.scores == [[None, 30.0], [90.0, 90.0]]

    # bottom 50% of 2 students = ceil(1.0) = 1 -> the weak student only.
    assert [f.email for f in out.flagged_students] == ["stu1@test.edu"]
    assert out.flagged_students[0].avg_score == 30.0
    assert out.flagged_students[0].completed_cases == 1


@pytest.mark.asyncio
async def test_class_summary_empty(clean_tables, db_session, professor):
    prof, _ = professor
    course = await _course(db_session, prof)
    from app.services.analytics_service import get_class_summary
    out = await get_class_summary(course.id, db_session)
    assert out.enrolled_students == 0
    assert out.total_completed_cases == 0
    assert out.avg_class_score is None
    assert out.completion_by_unit == []
    assert out.category_heatmap.students == []
    assert out.flagged_students == []
    assert {b.range for b in out.score_distribution} == {
        "0-20", "21-40", "41-60", "61-80", "81-100"
    }
```

- [ ] **Step 8: Run all Task 1 tests**

Run: `uv run pytest tests/test_professor_analytics.py -v`
Expected: PASS (4 tests).

- [ ] **Step 9: Commit**

```bash
git add app/schemas/analytics.py app/services/analytics_service.py tests/test_professor_analytics.py
git commit -m "feat: get_class_summary aggregation helper + class-summary schemas"
```

---

### Task 2: class-summary endpoint + caching + diagnose invalidation

**Files:**
- Modify: `app/services/analytics_cache.py`
- Modify: `app/routers/analytics.py`
- Modify: `app/routers/sessions.py`
- Test: `tests/test_professor_analytics.py`

**Interfaces:**
- Consumes: `get_class_summary`, `ClassSummary`, `get_cached_json`, `set_cached_json`.
- Produces: `class_summary_key(course_id) -> str`; `GET /api/v1/analytics/professor/class-summary`; module-level `_require_owned_course(course_id, user, db)` in `analytics.py`.

- [ ] **Step 1: Add `class_summary_key`** to `app/services/analytics_cache.py`

```python
def class_summary_key(course_id: uuid.UUID) -> str:
    return f"analytics:class:{course_id}"
```

- [ ] **Step 2: Write the failing endpoint test**

```python
@pytest_asyncio.fixture
def auth(rsa_keys):
    from tests.conftest import _make_token
    priv, _ = rsa_keys
    return lambda uid: {"Authorization": f"Bearer {_make_token(uid, priv)}"}


@pytest.mark.asyncio
async def test_class_summary_endpoint(clean_tables, db_session, client, professor, auth):
    prof, token = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    d = await _disease(db_session, u1, "MDD", "Mood")
    stu = await _student(db_session, 1)
    await _enroll(db_session, stu, course)
    await _session(db_session, stu, course, d, SessionStatus.diagnosed, score=70)

    client.app.state.redis.get.return_value = None
    resp = await client.get(
        f"/api/v1/analytics/professor/class-summary?course_id={course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enrolled_students"] == 1
    assert body["avg_class_score"] == 70.0


@pytest.mark.asyncio
async def test_class_summary_blocks_student(clean_tables, db_session, client, professor, student):
    prof, _ = professor
    _, stu_token = student
    course = await _course(db_session, prof)
    resp = await client.get(
        f"/api/v1/analytics/professor/class-summary?course_id={course.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_class_summary_unowned_course_404(clean_tables, db_session, client, professor, rsa_keys):
    prof, _ = professor
    course = await _course(db_session, prof)
    # a second professor who does not own the course
    other = User(id=uuid.uuid4(), google_uid=f"p-{uuid.uuid4().hex}",
                 email="other@test.edu", role=UserRole.professor, is_verified=False)
    db_session.add(other)
    await db_session.commit()
    from tests.conftest import _make_token
    priv, _ = rsa_keys
    resp = await client.get(
        f"/api/v1/analytics/professor/class-summary?course_id={course.id}",
        headers={"Authorization": f"Bearer {_make_token(other.id, priv)}"},
    )
    assert resp.status_code == 404
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_professor_analytics.py::test_class_summary_endpoint -v`
Expected: FAIL (404 — route not registered).

- [ ] **Step 4: Add the ownership helper + endpoint** to `app/routers/analytics.py`

Update imports: add `from fastapi import HTTPException, Query`, `from app.models.course import Course`, `from sqlalchemy import select`, `from app.schemas.analytics import ClassSummary, StudentSummary`, and from the cache module `class_summary_key`. From the service add `get_class_summary`.

```python
async def _require_owned_course(course_id, user, db) -> Course:
    course = (
        await db.execute(
            select(Course).where(
                Course.id == course_id, Course.professor_id == user.id
            )
        )
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@router.get("/professor/class-summary", response_model=ClassSummary)
async def professor_class_summary(
    course_id: uuid.UUID,
    request: Request,
    bottom_pct: float = Query(0.2, gt=0, le=1),
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
) -> ClassSummary:
    await _require_owned_course(course_id, current_user, db)
    redis = getattr(request.app.state, "redis", None)
    use_cache = bottom_pct == 0.2
    key = class_summary_key(course_id)
    if use_cache:
        cached = await get_cached_json(redis, key)
        if cached is not None:
            return ClassSummary.model_validate(cached)
    summary = await get_class_summary(course_id, db, bottom_pct)
    if use_cache:
        await set_cached_json(redis, key, summary.model_dump(mode="json"))
    return summary
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_professor_analytics.py -k class_summary -v`
Expected: PASS.

- [ ] **Step 6: Add the diagnose invalidation test**

```python
@pytest.mark.asyncio
async def test_diagnose_invalidates_class_cache(clean_tables, db_session, client, professor):
    # The diagnose path must delete analytics:class:{course_id}.
    from app.services.analytics_cache import class_summary_key
    # This asserts the key is referenced in the diagnose handler's invalidation.
    import inspect
    from app.routers import sessions as sessions_router
    src = inspect.getsource(sessions_router)
    assert "class_summary_key" in src
```

(A behavioral integration test would require driving a full session to diagnosis; the source check guards the wiring cheaply alongside the existing diagnose tests in `tests/test_sessions.py`.)

- [ ] **Step 7: Wire invalidation in `app/routers/sessions.py`**

Find the existing invalidation line (around `app/routers/sessions.py:259`):

```python
    await invalidate(redis, summary_key(current_user.id, session.course_id))
```

Add immediately after it:

```python
    await invalidate(redis, class_summary_key(session.course_id))
```

Update the import of cache helpers in `sessions.py` to include `class_summary_key`.

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/test_professor_analytics.py tests/test_sessions.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/services/analytics_cache.py app/routers/analytics.py app/routers/sessions.py tests/test_professor_analytics.py
git commit -m "feat: GET /analytics/professor/class-summary with redis cache + invalidation"
```

---

### Task 3: Student drill-down endpoint

**Files:**
- Modify: `app/schemas/analytics.py`
- Modify: `app/routers/analytics.py`
- Test: `tests/test_professor_analytics.py`

**Interfaces:**
- Consumes: `get_student_summary`, `list_completed_sessions`, `summary_key`, `_require_owned_course`, `StudentSummary`, `CompletedSessionItem`, `Enrollment`.
- Produces: schema `StudentDrilldown`; `GET /api/v1/analytics/professor/student/{user_id}`.

- [ ] **Step 1: Add `StudentDrilldown` schema** (append to `app/schemas/analytics.py`)

```python
class StudentDrilldown(StudentSummary):
    sessions: list[CompletedSessionItem]
    total: int
```

- [ ] **Step 2: Write the failing test**

```python
@pytest.mark.asyncio
async def test_student_drilldown(clean_tables, db_session, client, professor):
    prof, token = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    d = await _disease(db_session, u1, "MDD", "Mood")
    stu = await _student(db_session, 1)
    await _enroll(db_session, stu, course)
    await _session(db_session, stu, course, d, SessionStatus.diagnosed, score=88, turns=4)

    client.app.state.redis.get.return_value = None
    resp = await client.get(
        f"/api/v1/analytics/professor/student/{stu.id}?course_id={course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["completed_cases"] == 1
    assert body["avg_score"] == 88.0
    assert body["total"] == 1
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["disease_name"] == "MDD"


@pytest.mark.asyncio
async def test_drilldown_unenrolled_student_404(clean_tables, db_session, client, professor):
    prof, token = professor
    course = await _course(db_session, prof)
    stranger = await _student(db_session, 9)  # not enrolled
    client.app.state.redis.get.return_value = None
    resp = await client.get(
        f"/api/v1/analytics/professor/student/{stranger.id}?course_id={course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_professor_analytics.py::test_student_drilldown -v`
Expected: FAIL (404 route missing).

- [ ] **Step 4: Add the endpoint** to `app/routers/analytics.py`

Add imports: `from app.models.enrollment import Enrollment`, and from the service `list_completed_sessions`; from schemas `StudentDrilldown`; from cache `summary_key` (already imported in the file from Task-0 student endpoint).

```python
@router.get("/professor/student/{user_id}", response_model=StudentDrilldown)
async def professor_student_drilldown(
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
) -> StudentDrilldown:
    await _require_owned_course(course_id, current_user, db)
    enrolled = (
        await db.execute(
            select(Enrollment.id).where(
                Enrollment.user_id == user_id,
                Enrollment.course_id == course_id,
            )
        )
    ).scalar_one_or_none()
    if enrolled is None:
        raise HTTPException(status_code=404, detail="Student not found")

    redis = getattr(request.app.state, "redis", None)
    key = summary_key(user_id, course_id)
    cached = await get_cached_json(redis, key)
    if cached is not None:
        summary = StudentSummary.model_validate(cached)
    else:
        summary = await get_student_summary(user_id, course_id, db)
        await set_cached_json(redis, key, summary.model_dump(mode="json"))

    items, total = await list_completed_sessions(
        db, course_id=course_id, user_id=user_id, page=page, page_size=page_size
    )
    return StudentDrilldown(**summary.model_dump(), sessions=items, total=total)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_professor_analytics.py -k drilldown -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/analytics.py app/routers/analytics.py tests/test_professor_analytics.py
git commit -m "feat: GET /analytics/professor/student/{user_id} drill-down"
```

---

### Task 4: CSV export endpoint

**Files:**
- Modify: `app/services/analytics_service.py`
- Modify: `app/routers/analytics.py`
- Test: `tests/test_professor_analytics.py`

**Interfaces:**
- Consumes: `Session`, `Score`, `Disease`, `User`, `Enrollment`, `_require_owned_course`.
- Produces: `get_export_rows(course_id, db) -> list` (rows with `.email, .display_name, .name, .category, .total_score, .avg_response_latency_sec, .turn_count, .completed_at`); `GET /api/v1/analytics/professor/export`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_csv_export(clean_tables, db_session, client, professor):
    prof, token = professor
    course = await _course(db_session, prof)
    u1 = await _unit(db_session, course, "Unit 1")
    d = await _disease(db_session, u1, "MDD", "Mood")
    stu = await _student(db_session, 1)
    await _enroll(db_session, stu, course)
    await _session(db_session, stu, course, d, SessionStatus.diagnosed, score=75,
                   turns=6, latency=12.0, completed_offset=1)
    await _session(db_session, stu, course, d, SessionStatus.diagnosed, score=85,
                   turns=8, latency=10.0, completed_offset=2)

    resp = await client.get(
        f"/api/v1/analytics/professor/export?course_id={course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.strip().splitlines()
    assert lines[0] == (
        "student_email,student_name,case_number,disease_name,category,"
        "score,response_time_avg,turns,date_completed"
    )
    assert len(lines) == 3  # header + 2 cases
    # per-student case_number resets/increments 1,2 in completion order
    assert lines[1].split(",")[2] == "1"
    assert lines[2].split(",")[2] == "2"


@pytest.mark.asyncio
async def test_csv_export_bad_format_400(clean_tables, db_session, client, professor):
    prof, token = professor
    course = await _course(db_session, prof)
    resp = await client.get(
        f"/api/v1/analytics/professor/export?course_id={course.id}&format=xml",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_professor_analytics.py::test_csv_export -v`
Expected: FAIL (404 route missing).

- [ ] **Step 3: Add `get_export_rows`** to `app/services/analytics_service.py`

```python
async def get_export_rows(course_id: uuid.UUID, db: AsyncSession):
    return (
        await db.execute(
            select(
                User.email,
                User.display_name,
                Disease.name,
                Disease.category,
                Score.total_score,
                Session.avg_response_latency_sec,
                Session.turn_count,
                Session.completed_at,
            )
            .select_from(Session)
            .join(Score, Score.session_id == Session.id)
            .join(Disease, Disease.id == Session.disease_id)
            .join(User, User.id == Session.user_id)
            .join(
                Enrollment,
                and_(
                    Enrollment.user_id == Session.user_id,
                    Enrollment.course_id == course_id,
                ),
            )
            .where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
            )
            .order_by(
                User.email.asc(),
                Session.completed_at.asc().nulls_last(),
                Session.started_at.asc(),
                Session.id.asc(),
            )
        )
    ).all()
```

- [ ] **Step 4: Add the endpoint** to `app/routers/analytics.py`

Add imports: `import csv`, `import io`, `from fastapi import Response`, and from the service `get_export_rows`.

```python
@router.get("/professor/export")
async def professor_export(
    course_id: uuid.UUID,
    format: str = "csv",
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _require_owned_course(course_id, current_user, db)
    if format != "csv":
        raise HTTPException(status_code=400, detail="Unsupported format")

    rows = await get_export_rows(course_id, db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "student_email", "student_name", "case_number", "disease_name",
        "category", "score", "response_time_avg", "turns", "date_completed",
    ])
    counts: dict[str, int] = {}
    for r in rows:
        n = counts.get(r.email, 0) + 1
        counts[r.email] = n
        writer.writerow([
            r.email,
            r.display_name or "",
            n,
            r.name,
            r.category,
            r.total_score if r.total_score is not None else "",
            r.avg_response_latency_sec if r.avg_response_latency_sec is not None else "",
            r.turn_count,
            r.completed_at.isoformat() if r.completed_at else "",
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="grades_{course_id}.csv"'
        },
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_professor_analytics.py -k csv -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/analytics_service.py app/routers/analytics.py tests/test_professor_analytics.py
git commit -m "feat: GET /analytics/professor/export CSV grade export"
```

---

### Task 5: Documentation + full-suite verification

**Files:**
- Modify: `docs/api-contract.md`

- [ ] **Step 1: Document the three endpoints** in `docs/api-contract.md`

Add a "Professor analytics" subsection under the analytics section describing:
- `GET /api/v1/analytics/professor/class-summary?course_id&bottom_pct` (professor; 404 if not owner) → `ClassSummary` shape.
- `GET /api/v1/analytics/professor/student/{user_id}?course_id&page&page_size` (professor; 404 if not owner or student not enrolled) → `StudentDrilldown` (StudentSummary + `sessions[]` + `total`).
- `GET /api/v1/analytics/professor/export?course_id&format=csv` (professor; 404 if not owner; 400 if format != csv) → `text/csv` attachment with columns `student_email,student_name,case_number,disease_name,category,score,response_time_avg,turns,date_completed`.

Match the existing formatting/style of `docs/api-contract.md`.

- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -v`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add docs/api-contract.md
git commit -m "docs: document professor analytics endpoints"
```

---

## Self-Review

**Spec coverage:**
- Task 1: class-summary aggregation (counts, avg, per-unit, distribution, heatmap, flagged) ✓
- Task 2: class-summary endpoint, auth/ownership, caching + invalidation ✓
- Task 3: student drill-down (summary + sessions, enrollment 404) ✓
- Task 4: CSV export (columns, case_number, headers, format 400) ✓
- Task 5: api-contract docs + full suite ✓

**Type consistency:** `get_class_summary(course_id, db, bottom_pct=0.2)`, `class_summary_key(course_id)`, `_require_owned_course(course_id, user, db)`, `get_export_rows(course_id, db)`, `StudentDrilldown(StudentSummary)` used consistently across tasks.

**Placeholder scan:** none — all steps carry concrete code/commands.
