# Week 5 Backend Design — Unit Management, Messaging Window, Disease Document Diff

**Date:** 2026-05-30  
**Scope:** Backend tasks from `week-05.md` — unit release/close endpoints, messaging window validation, mid-semester disease document re-upload with diff

---

## 1. Goal

Three backend tasks this week:

1. **Unit management endpoints** — professors release/close units; students see only released units (blind to disease details)
2. **Messaging window validation** — add IANA timezone check and start < end guard to the existing course update endpoint, plus a utility for checking whether the current time is within a course's window
3. **Disease document diff** — re-uploading a disease document no longer wipes everything; instead it diffs against existing diseases (matching by unit label + disease name), soft-deletes removed ones, updates modified ones, and creates new ones

---

## 2. Architecture — Files Changed

### New files
| File | Purpose |
|------|---------|
| `app/routers/units.py` | Unit management + disease pool endpoints |
| `app/schemas/unit.py` | `UnitOut`, `UnitOutStudent`, `DiseaseOut` schemas |
| `app/services/document_diff.py` | Pure diff computation (no I/O, no SQLAlchemy) |
| `app/services/messaging.py` | `is_within_messaging_window` utility |
| `tests/test_units_router.py` | Integration tests for unit endpoints |
| `tests/test_document_diff.py` | Pure unit tests for diff service |
| `tests/test_messaging.py` | Pure unit tests for messaging window utility |

### Modified files
| File | Change |
|------|--------|
| `app/routers/disease_documents.py` | Upload computes diff preview; confirm applies diff instead of delete-all |
| `app/schemas/disease_document.py` | Add `DiffSummary`; update `DiseaseDocumentPreview` and `DiseaseDocumentConfirmResult` |
| `app/schemas/course.py` | Add IANA timezone + start < end validators to `CourseUpdate` |
| `app/main.py` | Register units router |
| `docs/api-contract.md` | New endpoints |
| `tests/test_courses_router.py` | Validation edge cases |
| `tests/test_disease_documents_router.py` | Diff behavior, confirm with released units |

### No migrations needed
All required columns already exist: `is_active` on `diseases`, `status`/`release_date` on `units`, `msg_window_start`/`msg_window_end`/`msg_timezone` on `courses`.

---

## 3. Task 1 — Unit Management Endpoints

### Router
`app/routers/units.py`, prefix `/courses/{course_id}`, tag `units`.

### Schemas (`app/schemas/unit.py`)

```python
class DiseaseOut(BaseModel):
    id: UUID
    name: str
    category: str
    difficulty_tier: int
    model_config = {"from_attributes": True}

class UnitOut(BaseModel):
    id: UUID
    course_id: UUID
    label: str
    status: UnitStatus          # "draft" | "released" | "closed"
    release_date: datetime | None
    disease_count: int          # = len(diseases); set by the router after loading the list
    diseases: list[DiseaseOut]  # active diseases only
    model_config = {"from_attributes": True}

class UnitOutStudent(BaseModel):
    id: UUID
    label: str
    status: Literal["released"]
    release_date: datetime
    disease_count: int          # COUNT query — student view does not load disease rows
```

### `GET /courses/{course_id}/units`

**Auth:** `get_current_user` (professor or student)

**Professor (owns course):** returns all units (draft/released/closed) with full `UnitOut` including `diseases` list (active diseases only).

**Student (enrolled in course):** returns only `released` units as `UnitOutStudent` — no disease names or details.

**Errors:** 404 if course not found or user is not owner/enrolled.

### `PUT /courses/{course_id}/units/{unit_id}/release`

**Auth:** professor, must own course

**Behavior:**
1. 404 if course not owned or unit not in this course
2. 409 if unit status is not `draft` (`"unit is not in draft status"`)
3. Set `status = "released"`, `release_date = now()`
4. Return updated `UnitOut`

### `PUT /courses/{course_id}/units/{unit_id}/close`

**Auth:** professor, must own course

**Behavior:**
1. 404 if course not owned or unit not in this course
2. 409 if unit status is not `released` (`"unit is not released"`)
3. Set `status = "closed"`
4. Return updated `UnitOut`

### `GET /courses/{course_id}/disease-pool`

**Auth:** professor only (not exposed to students)

**Behavior:** Returns `list[DiseaseOut]` for all `is_active=true` diseases belonging to `released` units in this course. Used by the Phase 2 scheduler to randomly select a disease for a new case.

**Errors:** 404 if course not owned.

---

## 4. Task 2 — Messaging Window Validation + Utility

### Schema validation (`app/schemas/course.py`)

Add to `CourseUpdate`:

```python
import zoneinfo
from pydantic import field_validator, model_validator

@field_validator("msg_timezone")
@classmethod
def check_timezone(cls, v: str | None) -> str | None:
    if v and v not in zoneinfo.available_timezones():
        raise ValueError(f"unknown timezone: {v!r}")
    return v

@model_validator(mode="after")
def check_window(self) -> "CourseUpdate":
    if self.msg_window_start and self.msg_window_end:
        if self.msg_window_start >= self.msg_window_end:
            raise ValueError("msg_window_start must be before msg_window_end")
    return self
```

Invalid inputs return FastAPI's standard 422. No router changes — `PUT /courses/{id}` already accepts these fields.

### Utility (`app/services/messaging.py`)

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from app.models.course import Course

def is_within_messaging_window(course: Course) -> bool:
    tz = ZoneInfo(course.msg_timezone)
    now = datetime.now(tz).time()
    return course.msg_window_start <= now <= course.msg_window_end
```

This will be called by the Phase 2 scheduler before sending messages to patients.

---

## 5. Task 3 — Disease Document Diff

### Diff service (`app/services/document_diff.py`)

Pure function — no SQLAlchemy, no I/O. Takes parsed data and a snapshot of existing DB state.

```python
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
    nudge_behavior: dict

@dataclass
class DiffResult:
    units_added: list[str]       # new unit labels not in DB
    units_orphaned: list[str]    # unit labels in DB but absent from new file
    diseases_added: int
    diseases_modified: int
    diseases_removed: int        # will be soft-deleted
```

**Matching key:** `(unit_label, disease_name)`. A disease is "modified" if it matches on this key but any other field differs. Only `is_active=true` diseases are considered when computing removed/modified counts.

**Function signature:**
```python
def compute_diff(
    parse_result: ParseResult,
    existing_units: list[str],                          # all unit labels in DB for this course
    existing_diseases: list[ExistingDisease],           # is_active=True only
) -> DiffResult
```

### Schema changes (`app/schemas/disease_document.py`)

```python
class DiffSummary(BaseModel):
    units_added: list[str]
    units_orphaned: list[str]
    diseases_added: int
    diseases_modified: int
    diseases_removed: int

class DiseaseDocumentPreview(BaseModel):
    document_id: UUID
    version: int
    units: list[UnitPreview]
    errors: list[ParseErrorOut]
    diff: DiffSummary | None    # None on first upload (version 1), populated on re-upload

class DiseaseDocumentConfirmResult(BaseModel):
    document_id: UUID
    version: int
    units_created: int
    diseases_created: int
    diff: DiffSummary           # always present; first upload has all zeros for modify/remove
```

### Upload endpoint changes

After parsing, if `max_version >= 1` (re-upload):
1. Query existing units (all statuses) and their `is_active=True` diseases for this course
2. Call `compute_diff(parse_result, existing_unit_labels, existing_diseases)`
3. Include `DiffSummary` in the preview response

On first upload (`max_version == 0`): `diff = None`.

### Confirm endpoint changes

**Remove** the "any released unit → 409" guard. Replace delete-all with a diff-apply transaction:

1. Load existing units (all statuses) + their `is_active=True` diseases via a JOIN (`Unit.id = Disease.unit_id`) so unit labels are available alongside disease rows
2. Build lookup: `{(unit.label, disease.name): Disease}` for existing active diseases
3. Parse file, compute diff
4. In a single transaction:
   - **New units** (unit label not in DB): insert `Unit` row + all diseases
   - **New diseases on existing units**: insert `Disease` row on the existing `Unit`
   - **Modified diseases**: update fields in place on the existing `Disease` row
     - NOTE: active cases are not affected — a case's system prompt is generated at session creation and cached. Updating a disease in the DB does not change the prompt for any case already in progress. New cases will use the updated disease data.
   - **Removed diseases** (`(unit_label, name)` present in DB but absent from new file): set `is_active = False`
   - **Orphaned units** (unit label in DB but absent from new file): set `is_active = False` on all their active diseases; leave the `Unit` row untouched (status/release_date unchanged)
5. Set `parsed_at = now()` on the `DiseaseDocument` row
6. Return `DiseaseDocumentConfirmResult` with diff counts

**Key invariant:** Released units and their diseases continue serving active cases. A disease that is updated in place keeps its DB row (and its `id` — no foreign key churn). A disease that is soft-deleted keeps its row for referential integrity; active cases on it continue running on the old system prompt.

---

## 6. API Contract Additions

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/v1/courses/{course_id}/units` | List units (professor: all; student: released only) | Bearer JWT |
| PUT | `/api/v1/courses/{course_id}/units/{unit_id}/release` | Release a draft unit | Bearer JWT (professor) |
| PUT | `/api/v1/courses/{course_id}/units/{unit_id}/close` | Close a released unit | Bearer JWT (professor) |
| GET | `/api/v1/courses/{course_id}/disease-pool` | All active diseases from released units (scheduler use) | Bearer JWT (professor) |

`PUT /api/v1/courses/{id}` gains stricter validation (422 on invalid timezone or start >= end).

---

## 7. Testing Plan

### `tests/test_units_router.py` (integration, `clean_tables`)
- GET /units as professor: all units (draft/released/closed), disease details included
- GET /units as student: only released units, no disease details
- GET /units as student not enrolled: 404
- GET /units as professor not owner: 404
- PUT release: draft → released, release_date set to now
- PUT release: 409 if already released
- PUT release: 409 if closed
- PUT close: released → closed
- PUT close: 409 if draft
- PUT close: 409 if already closed
- GET disease-pool: returns only is_active diseases from released units
- GET disease-pool as student: 403

### `tests/test_document_diff.py` (pure unit tests)
- First upload (no existing data): all zeros for modify/remove, all new diseases counted in added
- New disease added to existing unit: diseases_added=1
- Disease removed from existing unit: diseases_removed=1
- Disease fields changed: diseases_modified=1
- Entirely new unit label: units_added=[label]
- Unit label disappears from new file: units_orphaned=[label], its active diseases counted as removed
- Inactive diseases (is_active=False) not counted in removed

### `tests/test_disease_documents_router.py` (additions)
- Re-upload preview includes DiffSummary with correct counts
- First upload preview has diff=None
- Confirm with released units now succeeds (no 409)
- Confirm soft-deletes removed diseases (is_active=false), unit row unchanged
- Confirm updates modified disease fields in place (same disease id)
- Confirm adds new diseases to existing units
- Confirm creates new units with their diseases
- Orphaned unit: its diseases soft-deleted, unit row status unchanged

### `tests/test_courses_router.py` (additions)
- PUT /courses/{id} with msg_window_end <= msg_window_start: 422
- PUT /courses/{id} with invalid IANA timezone string: 422
- PUT /courses/{id} with valid timezone + valid window: 200

### `tests/test_messaging.py` (pure unit tests)
- Current time within window: True
- Current time outside window: False
- Boundary: exactly at start time: True
- Boundary: exactly at end time: True

---

## 8. Out of Scope for Week 5

- Case model and scheduler (Phase 2)
- Student-visible disease details (students remain blind to disease data)
- File cleanup for abandoned uploads on `/tmp`
- GCS storage migration
- Bulk release/close of multiple units in one request
