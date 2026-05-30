# Week 5 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add unit management endpoints (release/close/list/disease-pool), strengthen messaging window validation on the course update endpoint, and make disease document re-uploads diff-aware instead of wipe-and-replace.

**Architecture:** New `app/routers/units.py` handles all unit endpoints. A pure `app/services/document_diff.py` computes diffs from parsed data vs. DB snapshots — no I/O, fully unit-testable. The existing disease document upload and confirm endpoints are updated to call this service. Messaging window validation is added via Pydantic validators on `CourseUpdate`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, pytest-asyncio, `zoneinfo` (stdlib)

---

## File Map

| Action | File |
|--------|------|
| Create | `app/schemas/unit.py` |
| Create | `app/services/document_diff.py` |
| Create | `app/services/messaging.py` |
| Create | `app/routers/units.py` |
| Create | `tests/test_units_router.py` |
| Create | `tests/test_document_diff.py` |
| Create | `tests/test_messaging.py` |
| Create | `tests/fixtures/sample_diseases_v2.json` |
| Modify | `app/schemas/course.py` — add IANA + start<end validators |
| Modify | `app/schemas/disease_document.py` — add `DiffSummary`, update preview/confirm schemas |
| Modify | `app/routers/disease_documents.py` — upload computes diff; confirm applies diff |
| Modify | `app/main.py` — register units router |
| Modify | `docs/api-contract.md` — add new endpoints |
| Modify | `tests/test_courses_router.py` — add validation tests |
| Modify | `tests/test_disease_documents_router.py` — add diff behavior tests |

---

## Task 1: Messaging Window Validation on CourseUpdate

**Files:**
- Modify: `app/schemas/course.py`
- Modify: `tests/test_courses_router.py`

- [ ] **Step 1: Write the three failing tests**

Append to `tests/test_courses_router.py`. First read that file to find the correct place to add (after the last test). Add:

```python
async def test_update_course_window_start_after_end_returns_422(client, professor, clean_tables):
    _, token = professor
    create = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    course_id = create.json()["id"]

    resp = await client.put(
        f"/api/v1/courses/{course_id}",
        json={"msg_window_start": "22:00:00", "msg_window_end": "08:00:00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_update_course_invalid_timezone_returns_422(client, professor, clean_tables):
    _, token = professor
    create = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    course_id = create.json()["id"]

    resp = await client.put(
        f"/api/v1/courses/{course_id}",
        json={"msg_timezone": "Fake/NotReal"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_update_course_valid_messaging_settings(client, professor, clean_tables):
    _, token = professor
    create = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    course_id = create.json()["id"]

    resp = await client.put(
        f"/api/v1/courses/{course_id}",
        json={
            "msg_window_start": "09:00:00",
            "msg_window_end": "21:00:00",
            "msg_timezone": "America/Chicago",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["msg_window_start"] == "09:00:00"
    assert data["msg_window_end"] == "21:00:00"
    assert data["msg_timezone"] == "America/Chicago"
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
uv run pytest tests/test_courses_router.py::test_update_course_window_start_after_end_returns_422 tests/test_courses_router.py::test_update_course_invalid_timezone_returns_422 tests/test_courses_router.py::test_update_course_valid_messaging_settings -v
```

Expected: the first two tests FAIL (currently returns 200 instead of 422), the third may pass already.

- [ ] **Step 3: Add validators to CourseUpdate**

Replace the entire `app/schemas/course.py` with:

```python
from __future__ import annotations

import uuid
import zoneinfo
from datetime import datetime, time

from pydantic import BaseModel, field_validator, model_validator


class CourseCreate(BaseModel):
    title: str
    semester: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = None
    semester: str | None = None
    msg_window_start: time | None = None
    msg_window_end: time | None = None
    msg_timezone: str | None = None

    @field_validator("msg_timezone")
    @classmethod
    def check_timezone(cls, v: str | None) -> str | None:
        if v is not None and v not in zoneinfo.available_timezones():
            raise ValueError(f"unknown timezone: {v!r}")
        return v

    @model_validator(mode="after")
    def check_window(self) -> "CourseUpdate":
        if self.msg_window_start is not None and self.msg_window_end is not None:
            if self.msg_window_start >= self.msg_window_end:
                raise ValueError("msg_window_start must be before msg_window_end")
        return self


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

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
uv run pytest tests/test_courses_router.py::test_update_course_window_start_after_end_returns_422 tests/test_courses_router.py::test_update_course_invalid_timezone_returns_422 tests/test_courses_router.py::test_update_course_valid_messaging_settings -v
```

Expected: all three PASS.

- [ ] **Step 5: Run the full courses suite to confirm no regressions**

```bash
uv run pytest tests/test_courses_router.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/course.py tests/test_courses_router.py
git commit -m "feat: add IANA timezone and start<end validation to CourseUpdate"
```

---

## Task 2: is_within_messaging_window Utility

**Files:**
- Create: `app/services/messaging.py`
- Create: `tests/test_messaging.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_messaging.py`:

```python
from __future__ import annotations

from datetime import time
from unittest.mock import MagicMock

import pytest

from app.models.course import Course
from app.services.messaging import is_within_messaging_window


def _make_course(start: str, end: str, tz: str = "America/New_York") -> Course:
    course = MagicMock(spec=Course)
    sh, sm = start.split(":")
    eh, em = end.split(":")
    course.msg_window_start = time(int(sh), int(sm))
    course.msg_window_end = time(int(eh), int(em))
    course.msg_timezone = tz
    return course


def test_within_window():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(12, 0)) is True


def test_before_window():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(7, 59)) is False


def test_after_window():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(22, 1)) is False


def test_at_start_boundary():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(8, 0)) is True


def test_at_end_boundary():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(22, 0)) is True
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/test_messaging.py -v
```

Expected: FAIL with `ModuleNotFoundError` — `app.services.messaging` does not exist.

- [ ] **Step 3: Implement the utility**

Create `app/services/messaging.py`:

```python
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.models.course import Course


def is_within_messaging_window(course: Course, _now: time | None = None) -> bool:
    """Return True if the current time (in the course's timezone) is within the messaging window.

    The _now parameter exists for testing — pass it to override the current time.
    In production, omit it and the real wall-clock time is used.
    """
    if _now is None:
        tz = ZoneInfo(course.msg_timezone)
        _now = datetime.now(tz).time()
    return course.msg_window_start <= _now <= course.msg_window_end
```

- [ ] **Step 4: Run to confirm they pass**

```bash
uv run pytest tests/test_messaging.py -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/messaging.py tests/test_messaging.py
git commit -m "feat: add is_within_messaging_window utility"
```

---

## Task 3: Document Diff Service

**Files:**
- Create: `app/services/document_diff.py`
- Create: `tests/test_document_diff.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_document_diff.py`:

```python
from __future__ import annotations

import pytest

from app.services.disease_parser import ParsedDisease, ParsedUnit, ParseResult
from app.services.document_diff import DiffResult, ExistingDisease, compute_diff

_NUDGE = {"frequency": "rarely", "tone": "flat", "example": ""}


def _pd(name: str, difficulty_tier: int = 2, category: str = "Mood") -> ParsedDisease:
    return ParsedDisease(
        name=name,
        dsm_code=None,
        category=category,
        key_symptoms=["s1"],
        differentials=["d1"],
        difficulty_tier=difficulty_tier,
        speech_style="flat",
        nudge_behavior=_NUDGE,
    )


def _ed(name: str, unit_label: str, difficulty_tier: int = 2, category: str = "Mood") -> ExistingDisease:
    return ExistingDisease(
        name=name,
        unit_label=unit_label,
        dsm_code=None,
        category=category,
        key_symptoms=["s1"],
        differentials=["d1"],
        difficulty_tier=difficulty_tier,
        speech_style="flat",
        nudge_behavior=_NUDGE,
    )


def test_first_upload_all_added():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD"), _pd("GAD")])])
    result = compute_diff(pr, existing_units=[], existing_diseases=[])
    assert result.units_added == ["Unit 1"]
    assert result.units_orphaned == []
    assert result.diseases_added == 2
    assert result.diseases_modified == 0
    assert result.diseases_removed == 0


def test_new_disease_added_to_existing_unit():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD"), _pd("GAD")])])
    existing = [_ed("MDD", "Unit 1")]
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=existing)
    assert result.diseases_added == 1
    assert result.diseases_modified == 0
    assert result.diseases_removed == 0
    assert result.units_added == []


def test_disease_removed_from_existing_unit():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD")])])
    existing = [_ed("MDD", "Unit 1"), _ed("GAD", "Unit 1")]
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=existing)
    assert result.diseases_removed == 1
    assert result.diseases_added == 0
    assert result.diseases_modified == 0


def test_disease_modified():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD", difficulty_tier=3)])])
    existing = [_ed("MDD", "Unit 1", difficulty_tier=2)]
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=existing)
    assert result.diseases_modified == 1
    assert result.diseases_added == 0
    assert result.diseases_removed == 0


def test_unchanged_disease_not_counted():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD")])])
    existing = [_ed("MDD", "Unit 1")]
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=existing)
    assert result.diseases_modified == 0
    assert result.diseases_added == 0
    assert result.diseases_removed == 0


def test_new_unit_added():
    pr = ParseResult(units=[ParsedUnit(label="Unit 2", diseases=[_pd("GAD")])])
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=[])
    assert result.units_added == ["Unit 2"]
    assert result.diseases_added == 1


def test_unit_orphaned():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD")])])
    existing_diseases = [_ed("GAD", "Unit 2"), _ed("Panic", "Unit 2")]
    result = compute_diff(
        pr,
        existing_units=["Unit 1", "Unit 2"],
        existing_diseases=existing_diseases,
    )
    assert "Unit 2" in result.units_orphaned
    assert result.diseases_removed == 2


def test_inactive_diseases_not_in_existing_list():
    # Caller is responsible for filtering is_active=False before calling compute_diff.
    # An empty existing_diseases means no soft-deleted diseases count as removed.
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD")])])
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=[])
    assert result.diseases_removed == 0
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/test_document_diff.py -v
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the diff service**

Create `app/services/document_diff.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.disease_parser import ParsedDisease, ParseResult


@dataclass
class ExistingDisease:
    name: str
    unit_label: str
    dsm_code: str | None
    category: str
    key_symptoms: list[str]
    differentials: list[str]
    difficulty_tier: int
    speech_style: str
    nudge_behavior: dict[str, Any]


@dataclass
class DiffResult:
    units_added: list[str]
    units_orphaned: list[str]
    diseases_added: int
    diseases_modified: int
    diseases_removed: int


def _fields_equal(parsed: ParsedDisease, existing: ExistingDisease) -> bool:
    return (
        parsed.dsm_code == existing.dsm_code
        and parsed.category == existing.category
        and parsed.key_symptoms == existing.key_symptoms
        and parsed.differentials == existing.differentials
        and parsed.difficulty_tier == existing.difficulty_tier
        and parsed.speech_style == existing.speech_style
        and parsed.nudge_behavior == existing.nudge_behavior
    )


def compute_diff(
    parse_result: ParseResult,
    existing_units: list[str],
    existing_diseases: list[ExistingDisease],
) -> DiffResult:
    """Compute the diff between a new ParseResult and the current active DB state.

    existing_units: all unit labels currently in the DB for this course (any status).
    existing_diseases: only is_active=True diseases for this course.
    """
    existing_lookup: dict[tuple[str, str], ExistingDisease] = {
        (d.unit_label, d.name): d for d in existing_diseases
    }
    existing_unit_set = set(existing_units)
    new_unit_labels = {u.label for u in parse_result.units}

    units_added = [u.label for u in parse_result.units if u.label not in existing_unit_set]
    units_orphaned = [label for label in existing_units if label not in new_unit_labels]

    diseases_added = 0
    diseases_modified = 0
    seen_keys: set[tuple[str, str]] = set()

    for unit in parse_result.units:
        for disease in unit.diseases:
            key = (unit.label, disease.name)
            seen_keys.add(key)
            if key not in existing_lookup:
                diseases_added += 1
            elif not _fields_equal(disease, existing_lookup[key]):
                diseases_modified += 1

    diseases_removed = sum(1 for key in existing_lookup if key not in seen_keys)

    return DiffResult(
        units_added=units_added,
        units_orphaned=units_orphaned,
        diseases_added=diseases_added,
        diseases_modified=diseases_modified,
        diseases_removed=diseases_removed,
    )
```

- [ ] **Step 4: Run to confirm they pass**

```bash
uv run pytest tests/test_document_diff.py -v
```

Expected: all 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/document_diff.py tests/test_document_diff.py
git commit -m "feat: add document diff service"
```

---

## Task 4: Unit Schemas

**Files:**
- Create: `app/schemas/unit.py`

No direct tests — these schemas are exercised through the router tests in Task 5.

- [ ] **Step 1: Create the schema file**

Create `app/schemas/unit.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.unit import UnitStatus


class DiseaseOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    difficulty_tier: int

    model_config = {"from_attributes": True}


class UnitOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    label: str
    status: UnitStatus
    release_date: datetime | None
    disease_count: int
    diseases: list[DiseaseOut]

    model_config = {"from_attributes": True}


class UnitOutStudent(BaseModel):
    id: uuid.UUID
    label: str
    status: Literal["released"]
    release_date: datetime
    disease_count: int
```

- [ ] **Step 2: Commit**

```bash
git add app/schemas/unit.py
git commit -m "feat: add unit and disease output schemas"
```

---

## Task 5: Unit Management Router + Tests

**Files:**
- Create: `app/routers/units.py`
- Create: `tests/test_units_router.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_units_router.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from jose import jwt

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "rarely", "tone": "flat", "example": ""}


@pytest_asyncio.fixture
async def setup(professor, student, db_session):
    prof, prof_token = professor
    stu, stu_token = student

    course = Course(
        title="Psych 101",
        professor_id=prof.id,
        class_code="TSTCRS",
        is_active=True,
    )
    db_session.add(course)
    await db_session.flush()

    enrollment = Enrollment(user_id=stu.id, course_id=course.id)
    db_session.add(enrollment)

    unit_draft = Unit(course_id=course.id, label="Unit 1: Mood", status=UnitStatus.draft)
    unit_released = Unit(
        course_id=course.id,
        label="Unit 2: Anxiety",
        status=UnitStatus.released,
        release_date=datetime.now(timezone.utc),
    )
    unit_closed = Unit(course_id=course.id, label="Unit 3: Psychosis", status=UnitStatus.closed)
    db_session.add_all([unit_draft, unit_released, unit_closed])
    await db_session.flush()

    active_disease = Disease(
        unit_id=unit_released.id,
        name="GAD",
        category="Anxiety",
        key_symptoms=["worry"],
        differentials=["MDD"],
        difficulty_tier=2,
        speech_style="anxious",
        nudge_behavior=_NUDGE,
        is_active=True,
    )
    inactive_disease = Disease(
        unit_id=unit_released.id,
        name="Old Disease",
        category="Anxiety",
        key_symptoms=["x"],
        differentials=["y"],
        difficulty_tier=1,
        speech_style="flat",
        nudge_behavior=_NUDGE,
        is_active=False,
    )
    db_session.add_all([active_disease, inactive_disease])
    await db_session.commit()
    await db_session.refresh(unit_draft)
    await db_session.refresh(unit_released)
    await db_session.refresh(unit_closed)

    return course, unit_draft, unit_released, unit_closed, active_disease, prof_token, stu_token


async def test_list_units_professor_sees_all_statuses(client, setup):
    course, _, _, _, _, prof_token, _ = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    statuses = {u["status"] for u in resp.json()}
    assert statuses == {"draft", "released", "closed"}


async def test_list_units_professor_sees_disease_details(client, setup):
    course, _, _, _, active_disease, prof_token, _ = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    released = next(u for u in resp.json() if u["status"] == "released")
    assert released["disease_count"] == 1
    assert len(released["diseases"]) == 1
    assert released["diseases"][0]["name"] == "GAD"


async def test_list_units_student_sees_released_only(client, setup):
    course, _, _, _, _, _, stu_token = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "released"


async def test_list_units_student_has_no_diseases_field(client, setup):
    course, _, _, _, _, _, stu_token = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 200
    unit = resp.json()[0]
    assert "diseases" not in unit
    assert unit["disease_count"] == 1


async def test_list_units_professor_not_owner_returns_404(client, setup, db_session, rsa_keys):
    course, *_, prof_token, _ = setup
    private_pem, _ = rsa_keys
    other_prof = User(
        google_uid=f"p2-{uuid.uuid4().hex}",
        email=f"p2-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Other",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(other_prof)
    await db_session.commit()
    other_token = jwt.encode({"sub": str(other_prof.id)}, private_pem, algorithm="RS256")

    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


async def test_list_units_student_not_enrolled_returns_404(client, setup, db_session, rsa_keys):
    course, *_ = setup
    private_pem, _ = rsa_keys
    other_stu = User(
        google_uid=f"s2-{uuid.uuid4().hex}",
        email=f"s2-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.student,
        is_verified=True,
        display_name="Other",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(other_stu)
    await db_session.commit()
    other_token = jwt.encode({"sub": str(other_stu.id)}, private_pem, algorithm="RS256")

    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


async def test_release_draft_unit(client, setup):
    course, unit_draft, _, _, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_draft.id}/release",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "released"
    assert data["release_date"] is not None


async def test_release_already_released_returns_409(client, setup):
    course, _, unit_released, _, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_released.id}/release",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 409


async def test_release_closed_unit_returns_409(client, setup):
    course, _, _, unit_closed, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_closed.id}/release",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 409


async def test_close_released_unit(client, setup):
    course, _, unit_released, _, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_released.id}/close",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


async def test_close_draft_unit_returns_409(client, setup):
    course, unit_draft, _, _, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_draft.id}/close",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 409


async def test_close_already_closed_returns_409(client, setup):
    course, _, _, unit_closed, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_closed.id}/close",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 409


async def test_disease_pool_returns_active_released_only(client, setup):
    course, _, _, _, active_disease, prof_token, _ = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/disease-pool",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "GAD"


async def test_disease_pool_student_forbidden(client, setup):
    course, *_, stu_token = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/disease-pool",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/test_units_router.py -v
```

Expected: FAIL — router does not exist yet (likely 404 from FastAPI itself).

- [ ] **Step 3: Implement the unit router**

Create `app/routers/units.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from app.schemas.unit import DiseaseOut, UnitOut, UnitOutStudent

router = APIRouter(prefix="/courses/{course_id}", tags=["units"])


def _make_disease_out(disease: Disease) -> DiseaseOut:
    return DiseaseOut(
        id=disease.id,
        name=disease.name,
        category=disease.category,
        difficulty_tier=disease.difficulty_tier,
    )


def _make_unit_out(unit: Unit, diseases: list[Disease]) -> UnitOut:
    outs = [_make_disease_out(d) for d in diseases]
    return UnitOut(
        id=unit.id,
        course_id=unit.course_id,
        label=unit.label,
        status=unit.status,
        release_date=unit.release_date,
        disease_count=len(outs),
        diseases=outs,
    )


@router.get("/units", response_model=None)
async def list_units(
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == UserRole.professor:
        course = (
            await db.execute(
                select(Course).where(
                    Course.id == course_id, Course.professor_id == current_user.id
                )
            )
        ).scalar_one_or_none()
        if course is None:
            raise HTTPException(status_code=404, detail="Course not found")

        units = (
            await db.execute(select(Unit).where(Unit.course_id == course_id))
        ).scalars().all()

        result = []
        for unit in units:
            diseases = (
                await db.execute(
                    select(Disease).where(
                        Disease.unit_id == unit.id, Disease.is_active == True  # noqa: E712
                    )
                )
            ).scalars().all()
            result.append(_make_unit_out(unit, diseases))
        return result

    else:
        enrolled = (
            await db.execute(
                select(Enrollment).where(
                    Enrollment.course_id == course_id,
                    Enrollment.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if enrolled is None:
            raise HTTPException(status_code=404, detail="Course not found")

        units = (
            await db.execute(
                select(Unit).where(
                    Unit.course_id == course_id, Unit.status == UnitStatus.released
                )
            )
        ).scalars().all()

        result = []
        for unit in units:
            count = (
                await db.execute(
                    select(func.count()).select_from(Disease).where(
                        Disease.unit_id == unit.id, Disease.is_active == True  # noqa: E712
                    )
                )
            ).scalar_one()
            result.append(
                UnitOutStudent(
                    id=unit.id,
                    label=unit.label,
                    status="released",
                    release_date=unit.release_date,
                    disease_count=count,
                )
            )
        return result


async def _get_owned_unit(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> tuple[Unit, list[Disease]]:
    course = (
        await db.execute(
            select(Course).where(
                Course.id == course_id, Course.professor_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    unit = (
        await db.execute(
            select(Unit).where(Unit.id == unit_id, Unit.course_id == course_id)
        )
    ).scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    diseases = (
        await db.execute(
            select(Disease).where(
                Disease.unit_id == unit.id, Disease.is_active == True  # noqa: E712
            )
        )
    ).scalars().all()
    return unit, list(diseases)


@router.put("/units/{unit_id}/release", response_model=UnitOut)
async def release_unit(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    unit, diseases = await _get_owned_unit(course_id, unit_id, current_user, db)
    if unit.status != UnitStatus.draft:
        raise HTTPException(status_code=409, detail="Unit is not in draft status")
    unit.status = UnitStatus.released
    unit.release_date = datetime.now(timezone.utc)
    result = _make_unit_out(unit, diseases)  # build before commit — commit expires ORM objects
    await db.commit()
    return result


@router.put("/units/{unit_id}/close", response_model=UnitOut)
async def close_unit(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    unit, diseases = await _get_owned_unit(course_id, unit_id, current_user, db)
    if unit.status != UnitStatus.released:
        raise HTTPException(status_code=409, detail="Unit is not released")
    unit.status = UnitStatus.closed
    result = _make_unit_out(unit, diseases)  # build before commit — commit expires ORM objects
    await db.commit()
    return result


@router.get("/disease-pool", response_model=list[DiseaseOut])
async def get_disease_pool(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = (
        await db.execute(
            select(Course).where(
                Course.id == course_id, Course.professor_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    diseases = (
        await db.execute(
            select(Disease)
            .join(Unit, Disease.unit_id == Unit.id)
            .where(
                Unit.course_id == course_id,
                Unit.status == UnitStatus.released,
                Disease.is_active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    return [_make_disease_out(d) for d in diseases]
```

- [ ] **Step 4: Register the units router in main.py**

In `app/main.py`, add the import and `include_router` call. The file currently imports `auth, courses, disease_documents, enrollments, users`. Add `units`:

```python
from app.routers import auth, courses, disease_documents, enrollments, units, users
```

And after `app.include_router(enrollments.router, prefix="/api/v1")`, add:

```python
app.include_router(units.router, prefix="/api/v1")
```

- [ ] **Step 5: Run to confirm they pass**

```bash
uv run pytest tests/test_units_router.py -v
```

Expected: all 14 PASS.

- [ ] **Step 6: Run full suite to confirm no regressions**

```bash
uv run pytest -v
```

Expected: all existing tests plus new unit tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/units.py app/main.py tests/test_units_router.py
git commit -m "feat: add unit management and disease-pool endpoints"
```

---

## Task 6: Disease Document Schema — Add DiffSummary

**Files:**
- Modify: `app/schemas/disease_document.py`

No new tests — the updated schemas are exercised in Tasks 7 and 8. The `diff` field on `DiseaseDocumentPreview` is optional with a default of `None`, so all existing tests continue to pass unchanged.

- [ ] **Step 1: Update the schema file**

Replace `app/schemas/disease_document.py` with:

```python
from __future__ import annotations

import uuid

from pydantic import BaseModel


class UnitPreview(BaseModel):
    label: str
    disease_count: int
    diseases: list[str]


class ParseErrorOut(BaseModel):
    location: str
    message: str


class DiffSummary(BaseModel):
    units_added: list[str]
    units_orphaned: list[str]
    diseases_added: int
    diseases_modified: int
    diseases_removed: int


class DiseaseDocumentPreview(BaseModel):
    document_id: uuid.UUID
    version: int
    units: list[UnitPreview]
    errors: list[ParseErrorOut]
    diff: DiffSummary | None = None


class DiseaseDocumentConfirmResult(BaseModel):
    document_id: uuid.UUID
    version: int
    units_created: int
    diseases_created: int
    diff: DiffSummary
```

- [ ] **Step 2: Run the existing disease document tests to confirm no breakage**

```bash
uv run pytest tests/test_disease_documents_router.py -v
```

Expected: all existing tests PASS. (The `diff=None` default keeps them intact.)

- [ ] **Step 3: Commit**

```bash
git add app/schemas/disease_document.py
git commit -m "feat: add DiffSummary to disease document schemas"
```

---

## Task 7: Upload Endpoint — Diff Preview

**Files:**
- Modify: `app/routers/disease_documents.py`
- Create: `tests/fixtures/sample_diseases_v2.json`
- Modify: `tests/test_disease_documents_router.py`

- [ ] **Step 1: Create the v2 fixture**

Create `tests/fixtures/sample_diseases_v2.json`. This is a modified version of `sample_diseases.json` used to test re-upload diffs:
- Unit 1: Mood Disorders — **MDD modified** (difficulty_tier 2→3), **Bipolar I unchanged**, **Bipolar II removed**, **Cyclothymia added**
- Unit 2: Anxiety Disorders — **entire unit removed** (orphaned)
- Unit 3: Psychotic Disorders — **new unit** with one disease (Schizophrenia)

Expected diff vs. v1: `diseases_added=2`, `diseases_modified=1`, `diseases_removed=4`, `units_added=["Unit 3: Psychotic Disorders"]`, `units_orphaned=["Unit 2: Anxiety Disorders"]`

```json
{
  "units": [
    {
      "label": "Unit 1: Mood Disorders",
      "diseases": [
        {
          "name": "Major Depressive Disorder",
          "dsm_code": "F32.1",
          "category": "Mood Disorders",
          "key_symptoms": ["depressed mood", "anhedonia", "insomnia", "fatigue"],
          "differentials": ["Bipolar II", "Adjustment Disorder", "Dysthymia"],
          "difficulty_tier": 3,
          "speech_style": "flat",
          "nudge_behavior": {"frequency": "low", "tone": "withdrawn", "example": "I guess you're busy too"}
        },
        {
          "name": "Bipolar I Disorder",
          "dsm_code": "F31.1",
          "category": "Mood Disorders",
          "key_symptoms": ["mania", "elevated mood", "decreased need for sleep", "grandiosity"],
          "differentials": ["Bipolar II", "Schizoaffective Disorder", "Substance-Induced Mood Disorder"],
          "difficulty_tier": 3,
          "speech_style": "pressured",
          "nudge_behavior": {"frequency": "high", "tone": "urgent", "example": "I just had the BEST idea, you have to hear it"}
        },
        {
          "name": "Cyclothymia",
          "dsm_code": "F34.0",
          "category": "Mood Disorders",
          "key_symptoms": ["mood fluctuation", "hypomanic symptoms", "mild depression"],
          "differentials": ["Bipolar II", "Personality Disorder"],
          "difficulty_tier": 2,
          "speech_style": "variable",
          "nudge_behavior": {"frequency": "medium", "tone": "uneven", "example": "Things are just up and down lately"}
        }
      ]
    },
    {
      "label": "Unit 3: Psychotic Disorders",
      "diseases": [
        {
          "name": "Schizophrenia",
          "dsm_code": "F20.9",
          "category": "Psychotic Disorders",
          "key_symptoms": ["hallucinations", "delusions", "disorganized speech", "negative symptoms"],
          "differentials": ["Schizoaffective Disorder", "Brief Psychotic Disorder", "Substance-Induced Psychosis"],
          "difficulty_tier": 4,
          "speech_style": "disorganized",
          "nudge_behavior": {"frequency": "low", "tone": "confused", "example": "They're watching me again"}
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_disease_documents_router.py`:

```python
def _sample_v2_bytes() -> bytes:
    return (Path(__file__).parent / "fixtures" / "sample_diseases_v2.json").read_bytes()


async def test_first_upload_diff_is_none(client, professor):
    _, token = professor
    course = await _create_course(client, token)

    files = {"file": ("sample.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["diff"] is None


async def test_reupload_preview_includes_diff(client, professor):
    _, token = professor
    course = await _create_course(client, token)

    # First upload + confirm to create DB state
    files = {"file": ("sample.json", _sample_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Second upload (v2) — should return diff
    files2 = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files2,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    diff = resp.json()["diff"]
    assert diff is not None
    assert diff["diseases_added"] == 2
    assert diff["diseases_modified"] == 1
    assert diff["diseases_removed"] == 4
    assert "Unit 3: Psychotic Disorders" in diff["units_added"]
    assert "Unit 2: Anxiety Disorders" in diff["units_orphaned"]
```

- [ ] **Step 3: Run to confirm they fail**

```bash
uv run pytest tests/test_disease_documents_router.py::test_first_upload_diff_is_none tests/test_disease_documents_router.py::test_reupload_preview_includes_diff -v
```

Expected: `test_first_upload_diff_is_none` likely FAILS (response has no `diff` key), `test_reupload_preview_includes_diff` also FAILS.

- [ ] **Step 4: Update the upload endpoint**

In `app/routers/disease_documents.py`, add the necessary imports at the top:

```python
from app.models.unit import Unit
from app.schemas.disease_document import (
    DiffSummary,
    DiseaseDocumentConfirmResult,
    DiseaseDocumentPreview,
    ParseErrorOut,
    UnitPreview,
)
from app.services.document_diff import ExistingDisease, compute_diff
```

Replace the `upload_disease_document` function body (everything after the course ownership check and parsing) with the version that computes a diff on re-upload:

```python
@router.post("", response_model=DiseaseDocumentPreview)
async def upload_disease_document(
    course_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = await _get_owned_course(course_id, current_user, db)
    ext = _extract_extension(file.filename)
    raw = await file.read()

    result = disease_parser.parse(file.filename, raw)

    max_version = (
        await db.execute(
            select(func.coalesce(func.max(DiseaseDocument.version), 0)).where(
                DiseaseDocument.course_id == course.id
            )
        )
    ).scalar_one()
    next_version = max_version + 1

    file_url = file_storage.save_upload(course.id, next_version, ext, raw)

    doc = DiseaseDocument(
        course_id=course.id,
        uploaded_by=current_user.id,
        file_url=file_url,
        version=next_version,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    diff: DiffSummary | None = None
    if max_version >= 1:
        existing_units = (
            await db.execute(select(Unit).where(Unit.course_id == course.id))
        ).scalars().all()
        existing_unit_labels = [u.label for u in existing_units]
        unit_id_to_label = {u.id: u.label for u in existing_units}

        raw_diseases = (
            await db.execute(
                select(Disease)
                .join(Unit, Disease.unit_id == Unit.id)
                .where(Unit.course_id == course.id, Disease.is_active == True)  # noqa: E712
            )
        ).scalars().all()

        existing_disease_list = [
            ExistingDisease(
                name=d.name,
                unit_label=unit_id_to_label[d.unit_id],
                dsm_code=d.dsm_code,
                category=d.category,
                key_symptoms=d.key_symptoms,
                differentials=d.differentials,
                difficulty_tier=d.difficulty_tier,
                speech_style=d.speech_style,
                nudge_behavior=d.nudge_behavior,
            )
            for d in raw_diseases
        ]

        diff_result = compute_diff(result, existing_unit_labels, existing_disease_list)
        diff = DiffSummary(
            units_added=diff_result.units_added,
            units_orphaned=diff_result.units_orphaned,
            diseases_added=diff_result.diseases_added,
            diseases_modified=diff_result.diseases_modified,
            diseases_removed=diff_result.diseases_removed,
        )

    return DiseaseDocumentPreview(
        document_id=doc.id,
        version=doc.version,
        units=[
            UnitPreview(
                label=u.label,
                disease_count=len(u.diseases),
                diseases=[d.name for d in u.diseases],
            )
            for u in result.units
        ],
        errors=[ParseErrorOut(location=e.location, message=e.message) for e in result.errors],
        diff=diff,
    )
```

- [ ] **Step 5: Run to confirm they pass**

```bash
uv run pytest tests/test_disease_documents_router.py::test_first_upload_diff_is_none tests/test_disease_documents_router.py::test_reupload_preview_includes_diff -v
```

Expected: both PASS.

- [ ] **Step 6: Run the full disease document suite to confirm no regressions**

```bash
uv run pytest tests/test_disease_documents_router.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/disease_documents.py tests/test_disease_documents_router.py tests/fixtures/sample_diseases_v2.json
git commit -m "feat: add diff preview to disease document upload endpoint"
```

---

## Task 8: Confirm Endpoint — Diff Apply

**Files:**
- Modify: `app/routers/disease_documents.py`
- Modify: `tests/test_disease_documents_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_disease_documents_router.py`:

```python
async def _upload_and_confirm(client, token, course_id, file_bytes):
    files = {"file": ("sample.json", file_bytes, "application/json")}
    upload = await client.post(
        f"/api/v1/courses/{course_id}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert upload.status_code == 200
    confirm = await client.post(
        f"/api/v1/courses/{course_id}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert confirm.status_code == 200
    return confirm.json()


async def test_confirm_first_upload_diff_has_only_added(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    result = await _upload_and_confirm(client, token, course["id"], _sample_bytes())
    diff = result["diff"]
    assert diff["diseases_added"] == 6
    assert diff["diseases_modified"] == 0
    assert diff["diseases_removed"] == 0
    assert len(diff["units_added"]) == 2
    assert diff["units_orphaned"] == []


async def test_confirm_with_released_unit_succeeds(client, professor, db_session):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    # Release a unit directly in DB
    async with _fresh_session() as s:
        from app.models.unit import Unit, UnitStatus
        from datetime import datetime, timezone
        from sqlalchemy import select
        units = (await s.execute(select(Unit))).scalars().all()
        units[0].status = UnitStatus.released
        units[0].release_date = datetime.now(timezone.utc)
        await s.commit()

    # Re-upload should now succeed (no 409)
    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    confirm = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert confirm.status_code == 200


async def test_confirm_reupload_soft_deletes_removed_diseases(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    result = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 200
    diff = result.json()["diff"]
    assert diff["diseases_removed"] == 4

    # Verify via disease-pool that removed diseases are gone
    pool_resp = await client.get(
        f"/api/v1/courses/{course['id']}/disease-pool",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Need to release a unit first to see the pool — but we can verify via units endpoint
    # The total active disease count after v2 confirm:
    # Unit 1: MDD (modified), Bipolar I (unchanged), Cyclothymia (new) = 3
    # Unit 3: Schizophrenia (new) = 1
    # Total active = 4 (Bipolar II, GAD, Panic, Social Anxiety are soft-deleted)
    units_resp = await client.get(
        f"/api/v1/courses/{course['id']}/units",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert units_resp.status_code == 200
    total_active = sum(u["disease_count"] for u in units_resp.json())
    assert total_active == 4


async def test_confirm_reupload_updates_modified_disease_in_place(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    # Record MDD's disease ID before re-upload
    async with _fresh_session() as s:
        from app.models.disease import Disease
        from sqlalchemy import select
        mdd = (await s.execute(select(Disease).where(Disease.name == "Major Depressive Disorder"))).scalar_one()
        mdd_id_before = mdd.id
        assert mdd.difficulty_tier == 2

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )

    async with _fresh_session() as s:
        from app.models.disease import Disease
        from sqlalchemy import select
        mdd = (await s.execute(select(Disease).where(Disease.name == "Major Depressive Disorder"))).scalar_one()
        # Same DB row (same id), updated field
        assert mdd.id == mdd_id_before
        assert mdd.difficulty_tier == 3


async def test_confirm_reupload_creates_new_diseases(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    result = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 200
    diff = result.json()["diff"]
    assert diff["diseases_added"] == 2  # Cyclothymia + Schizophrenia


async def test_confirm_reupload_creates_new_unit(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    result = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 200
    diff = result.json()["diff"]
    assert "Unit 3: Psychotic Disorders" in diff["units_added"]

    units_resp = await client.get(
        f"/api/v1/courses/{course['id']}/units",
        headers={"Authorization": f"Bearer {token}"},
    )
    labels = [u["label"] for u in units_resp.json()]
    assert "Unit 3: Psychotic Disorders" in labels


async def test_confirm_orphaned_unit_leaves_unit_row_intact(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Unit 2 row still exists (professor can see orphaned units)
    units_resp = await client.get(
        f"/api/v1/courses/{course['id']}/units",
        headers={"Authorization": f"Bearer {token}"},
    )
    labels = [u["label"] for u in units_resp.json()]
    assert "Unit 2: Anxiety Disorders" in labels
    orphaned_unit = next(u for u in units_resp.json() if u["label"] == "Unit 2: Anxiety Disorders")
    # All its diseases were soft-deleted
    assert orphaned_unit["disease_count"] == 0
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/test_disease_documents_router.py::test_confirm_first_upload_diff_has_only_added tests/test_disease_documents_router.py::test_confirm_reupload_soft_deletes_removed_diseases -v
```

Expected: FAIL — `confirm` response has no `diff` key.

- [ ] **Step 3: Replace the confirm endpoint**

Replace the entire `confirm_disease_document` function in `app/routers/disease_documents.py` with:

```python
@router.post("/confirm", response_model=DiseaseDocumentConfirmResult)
async def confirm_disease_document(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = await _get_owned_course(course_id, current_user, db)

    doc_result = await db.execute(
        select(DiseaseDocument)
        .where(DiseaseDocument.course_id == course.id, DiseaseDocument.parsed_at.is_(None))
        .order_by(DiseaseDocument.uploaded_at.desc())
        .limit(1)
        .with_for_update()
    )
    doc = doc_result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="No pending upload to confirm")

    if not file_storage.upload_exists(doc.file_url):
        raise HTTPException(status_code=410, detail="Upload file expired, please re-upload")

    raw = file_storage.read_upload(doc.file_url)
    filename = f"doc.{doc.file_url.rsplit('.', 1)[-1]}"
    parse_result = disease_parser.parse(filename, raw)

    if parse_result.errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "File has parse errors; cannot confirm",
                "errors": [
                    {"location": e.location, "message": e.message} for e in parse_result.errors
                ],
            },
        )

    # Load existing units (all statuses) with a row lock
    existing_units = (
        await db.execute(
            select(Unit).where(Unit.course_id == course.id).with_for_update()
        )
    ).scalars().all()
    existing_unit_map = {u.label: u for u in existing_units}
    unit_id_to_label = {u.id: u.label for u in existing_units}

    # Load active diseases via JOIN (locked via units)
    raw_diseases = (
        await db.execute(
            select(Disease)
            .join(Unit, Disease.unit_id == Unit.id)
            .where(Unit.course_id == course.id, Disease.is_active == True)  # noqa: E712
        )
    ).scalars().all()

    existing_disease_map: dict[tuple[str, str], Disease] = {
        (unit_id_to_label[d.unit_id], d.name): d for d in raw_diseases
    }
    existing_disease_list = [
        ExistingDisease(
            name=d.name,
            unit_label=unit_id_to_label[d.unit_id],
            dsm_code=d.dsm_code,
            category=d.category,
            key_symptoms=d.key_symptoms,
            differentials=d.differentials,
            difficulty_tier=d.difficulty_tier,
            speech_style=d.speech_style,
            nudge_behavior=d.nudge_behavior,
        )
        for d in raw_diseases
    ]

    diff_result = compute_diff(
        parse_result,
        list(existing_unit_map.keys()),
        existing_disease_list,
    )

    units_created = 0
    diseases_created = 0
    seen_keys: set[tuple[str, str]] = set()

    for parsed_unit in parse_result.units:
        if parsed_unit.label not in existing_unit_map:
            unit = Unit(course_id=course.id, label=parsed_unit.label)
            db.add(unit)
            await db.flush()
            existing_unit_map[parsed_unit.label] = unit
            units_created += 1

        unit = existing_unit_map[parsed_unit.label]
        for parsed_disease in parsed_unit.diseases:
            key = (parsed_unit.label, parsed_disease.name)
            seen_keys.add(key)
            if key not in existing_disease_map:
                db.add(Disease(
                    unit_id=unit.id,
                    name=parsed_disease.name,
                    dsm_code=parsed_disease.dsm_code,
                    category=parsed_disease.category,
                    key_symptoms=parsed_disease.key_symptoms,
                    differentials=parsed_disease.differentials,
                    difficulty_tier=parsed_disease.difficulty_tier,
                    speech_style=parsed_disease.speech_style,
                    nudge_behavior=parsed_disease.nudge_behavior,
                ))
                diseases_created += 1
            else:
                # Update modified disease in place. Active cases are not affected —
                # a case's system prompt is generated at session creation and cached.
                # Updating a disease here does not change any in-progress case.
                # New cases will use the updated disease data.
                existing_d = existing_disease_map[key]
                existing_d.dsm_code = parsed_disease.dsm_code
                existing_d.category = parsed_disease.category
                existing_d.key_symptoms = parsed_disease.key_symptoms
                existing_d.differentials = parsed_disease.differentials
                existing_d.difficulty_tier = parsed_disease.difficulty_tier
                existing_d.speech_style = parsed_disease.speech_style
                existing_d.nudge_behavior = parsed_disease.nudge_behavior

    # Soft-delete diseases not present in the new file (including orphaned units' diseases)
    for key, disease in existing_disease_map.items():
        if key not in seen_keys:
            disease.is_active = False

    doc.parsed_at = datetime.now(timezone.utc)
    await db.commit()

    return DiseaseDocumentConfirmResult(
        document_id=doc.id,
        version=doc.version,
        units_created=units_created,
        diseases_created=diseases_created,
        diff=DiffSummary(
            units_added=diff_result.units_added,
            units_orphaned=diff_result.units_orphaned,
            diseases_added=diff_result.diseases_added,
            diseases_modified=diff_result.diseases_modified,
            diseases_removed=diff_result.diseases_removed,
        ),
    )
```

- [ ] **Step 4: Run the new confirm tests**

```bash
uv run pytest tests/test_disease_documents_router.py::test_confirm_first_upload_diff_has_only_added tests/test_disease_documents_router.py::test_confirm_with_released_unit_succeeds tests/test_disease_documents_router.py::test_confirm_reupload_soft_deletes_removed_diseases tests/test_disease_documents_router.py::test_confirm_reupload_updates_modified_disease_in_place tests/test_disease_documents_router.py::test_confirm_reupload_creates_new_diseases tests/test_disease_documents_router.py::test_confirm_reupload_creates_new_unit tests/test_disease_documents_router.py::test_confirm_orphaned_unit_leaves_unit_row_intact -v
```

Expected: all 7 PASS.

- [ ] **Step 5: Run the full disease document suite**

```bash
uv run pytest tests/test_disease_documents_router.py -v
```

Expected: all PASS (old tests that checked the 409 for released units are now gone — but verify no pre-existing test asserts that behavior; if one does, delete it since the spec removes that guard).

- [ ] **Step 6: Run the full test suite**

```bash
uv run pytest -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/disease_documents.py tests/test_disease_documents_router.py
git commit -m "feat: replace disease document confirm delete-all with diff-apply"
```

---

## Task 9: Update API Contract

**Files:**
- Modify: `docs/api-contract.md`

- [ ] **Step 1: Add the new unit endpoints to the Units section**

Add a new `## Units` section to `docs/api-contract.md` after the Disease Documents section:

```markdown
## Units

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| GET | `/api/v1/courses/{course_id}/units` | List units (professor: all statuses with diseases; student: released only, no disease details) | Bearer JWT | ✅ Week 5 |
| PUT | `/api/v1/courses/{course_id}/units/{unit_id}/release` | Release a draft unit (sets status=released, release_date=now) | Bearer JWT (professor) | ✅ Week 5 |
| PUT | `/api/v1/courses/{course_id}/units/{unit_id}/close` | Close a released unit | Bearer JWT (professor) | ✅ Week 5 |
| GET | `/api/v1/courses/{course_id}/disease-pool` | All active diseases from released units — used by scheduler | Bearer JWT (professor) | ✅ Week 5 |

### GET /api/v1/courses/{course_id}/units
**Professor (course owner):** Returns all units (draft/released/closed) with `diseases` list (active only).  
**Student (enrolled):** Returns only `released` units. No `diseases` field — students are blind to disease details.  
**Errors:** 404 if course not found or caller is not owner/enrolled.

### PUT /api/v1/courses/{course_id}/units/{unit_id}/release
**Role required:** professor (must own course)  
**Response:** updated `UnitOut` with `status: "released"` and `release_date` set  
**Errors:** 404 not found, 409 unit is not in draft status

### PUT /api/v1/courses/{course_id}/units/{unit_id}/close
**Role required:** professor (must own course)  
**Response:** updated `UnitOut` with `status: "closed"`  
**Errors:** 404 not found, 409 unit is not released

### GET /api/v1/courses/{course_id}/disease-pool
**Role required:** professor (must own course). Not exposed to students.  
**Response:** `list[DiseaseOut]` — id, name, category, difficulty_tier  
**Errors:** 404 not found or not owner
```

Also update the Disease Documents section to note that `PUT /api/v1/courses/{id}` now validates messaging settings (add a note to the existing entry).

- [ ] **Step 2: Commit**

```bash
git add docs/api-contract.md
git commit -m "docs: add Week 5 endpoints to API contract"
```
