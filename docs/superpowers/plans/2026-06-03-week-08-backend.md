# Week 8 Backend Implementation Plan — Diagnosis Submission + Grading

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let students submit a diagnosis for their AI-patient case; a Gemini-backed grading engine evaluates it, returns a score + feedback on success or a subtle hint on failure, marks the session `diagnosed` and reveals the unit/disease when correct.

**Architecture:** Five layers mirroring the Week 7 session feature — a new `Score` model + migration; schemas extended in `schemas/session.py`; two new `llm_gateway` methods (`grade_diagnosis` with structured JSON output, `generate_hint` plain text); a new `grading_service.py` (pure time-score math + DB orchestration that returns an uncommitted `Score`); and `routers/sessions.py` extended with `POST /sessions/{id}/diagnose` plus a reveal block on `GET /sessions/{id}`. The router owns the transaction boundary (persist `Score` only when correct).

**Tech Stack:** FastAPI async, SQLAlchemy 2.0 async, google-genai 2.7 (sync SDK wrapped in `asyncio.to_thread`, structured JSON via `response_mime_type` + `response_schema`), Pydantic v2, Alembic, pytest + httpx AsyncClient.

**Spec:** `docs/superpowers/specs/2026-06-03-week-08-backend-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `app/models/score.py` | `Score` SQLAlchemy model (scores table) |
| Modify | `app/models/__init__.py` | Re-export `Score` |
| Create | `alembic/versions/<rev>_add_scores_table.py` | Migration for scores table |
| Modify | `tests/conftest.py` | Add `scores` to the TRUNCATE list |
| Modify | `app/schemas/session.py` | `DiagnosisCreate`, `ScoreOut`, `RevealOut`, `DiagnosisResult`; extend `SessionOut` |
| Modify | `app/services/llm_gateway.py` | `grade_diagnosis`, `generate_hint` |
| Create | `app/services/grading_service.py` | `compute_response_time_score`, `grade_diagnosis`, `generate_diagnosis_hint` |
| Modify | `app/routers/sessions.py` | `POST /sessions/{id}/diagnose`; reveal on `GET /sessions/{id}` |
| Create | `tests/test_grading_service.py` | Unit tests (time score + orchestration, mocked gateway) |
| Modify | `tests/test_llm_gateway.py` | Tests for `grade_diagnosis` + `generate_hint` |
| Modify | `tests/test_sessions_router.py` | Integration tests for diagnose + reveal |
| Modify | `docs/api-contract.md` | Document the new endpoint + GET changes |

> Note: `app/main.py` already registers `sessions.router` — no change needed there.

---

## Task 1: Score model + migration

**Files:**
- Create: `app/models/score.py`
- Modify: `app/models/__init__.py`
- Modify: `tests/conftest.py`
- Create: `alembic/versions/<rev>_add_scores_table.py` (via autogenerate)

- [ ] **Step 1: Write the failing test**

Create `tests/test_score_model.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.course import Course
from app.models.disease import Disease
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}


@pytest_asyncio.fixture
async def a_session(professor, student, db_session):
    prof, _ = professor
    stu, _ = student
    course = Course(title="P", professor_id=prof.id, class_code="SCR123", is_active=True)
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="Unit 1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(unit_id=unit.id, name="MDD", category="Mood",
                      key_symptoms=["low mood"], differentials=["GAD"],
                      difficulty_tier=2, speech_style="flat", nudge_behavior=_NUDGE)
    db_session.add(disease)
    await db_session.flush()
    session = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                      started_at=datetime.now(timezone.utc), status=SessionStatus.active)
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def test_score_persists_and_reads_back(a_session, db_session):
    score = Score(
        session_id=a_session.id, primary_dx="Major Depressive Disorder",
        differentials=["Bipolar II"], justification="x" * 60, is_correct=True,
        rubric_score=88.0, response_time_score=100.0, total_score=91.6,
        feedback_text="Good work.", graded_at=datetime.now(timezone.utc),
    )
    db_session.add(score)
    await db_session.commit()

    got = (await db_session.execute(select(Score).where(Score.session_id == a_session.id))).scalar_one()
    assert got.primary_dx == "Major Depressive Disorder"
    assert got.differentials == ["Bipolar II"]
    assert got.is_correct is True
    assert got.total_score == 91.6


async def test_score_session_id_unique(a_session, db_session):
    db_session.add(Score(session_id=a_session.id, primary_dx="A", differentials=[],
                         justification="x" * 60))
    await db_session.commit()
    db_session.add(Score(session_id=a_session.id, primary_dx="B", differentials=[],
                         justification="y" * 60))
    with pytest.raises(IntegrityError):
        await db_session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_score_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.score'`.

- [ ] **Step 3: Create the Score model**

Create `app/models/score.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    primary_dx: Mapped[str] = mapped_column(String(255), nullable=False)
    differentials: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rubric_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    response_time_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 4: Re-export Score from the models package**

In `app/models/__init__.py`, add the import after the `Session` import line and add `"Score"` to `__all__`:

```python
from app.models.score import Score
from app.models.session import Session, SessionStatus
```

```python
__all__ = [
    "User",
    "Course",
    "Enrollment",
    "Unit",
    "UnitStatus",
    "Disease",
    "DiseaseDocument",
    "Session",
    "SessionStatus",
    "Score",
    "Message",
    "MessageRole",
]
```

- [ ] **Step 5: Add `scores` to the test TRUNCATE list**

In `tests/conftest.py`, update the `TRUNCATE TABLE` statement in `_truncate_all()` to include `scores` first (it FK-references sessions):

```python
        await conn.execute(
            "TRUNCATE TABLE scores, messages, sessions, disease_documents, diseases, units, enrollments, courses, users CASCADE"
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_score_model.py -v`
Expected: PASS (2 tests). The test DB builds tables from `Base.metadata`, so the model alone makes them green.

- [ ] **Step 7: Generate the Alembic migration**

Run: `uv run alembic revision --autogenerate -m "add scores table"`

Open the generated file in `alembic/versions/`. **Verify by hand** (autogenerate can miss these):
- `sa.Column('session_id', ...)` has `sa.ForeignKey('sessions.id', ondelete='CASCADE')`.
- There is a `sa.UniqueConstraint('session_id', ...)` **or** the column is created with `unique=True`. If missing, add to `op.create_table`:
  `sa.UniqueConstraint('session_id', name='uq_scores_session_id')`.
- `differentials` is `postgresql.JSONB` and `nullable=False`.

- [ ] **Step 8: Apply the migration to the dev DB**

Run: `uv run alembic upgrade head`
Expected: `Running upgrade ... add scores table`, no error.

- [ ] **Step 9: Commit**

```bash
git add app/models/score.py app/models/__init__.py tests/conftest.py tests/test_score_model.py alembic/versions/
git commit -m "feat: add scores model + migration"
```

---

## Task 2: Diagnosis schemas

**Files:**
- Modify: `app/schemas/session.py`
- Test: `tests/test_diagnosis_schemas.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_diagnosis_schemas.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.session import DiagnosisCreate


def test_valid_diagnosis():
    d = DiagnosisCreate(
        primary_dx="Major Depressive Disorder",
        differentials=["Bipolar II", "Adjustment Disorder"],
        justification="x" * 50,
    )
    assert d.primary_dx == "Major Depressive Disorder"
    assert len(d.differentials) == 2


def test_differentials_default_empty():
    d = DiagnosisCreate(primary_dx="MDD", justification="x" * 50)
    assert d.differentials == []


def test_primary_dx_required_nonempty():
    with pytest.raises(ValidationError):
        DiagnosisCreate(primary_dx="", justification="x" * 50)


def test_justification_min_length_50():
    with pytest.raises(ValidationError):
        DiagnosisCreate(primary_dx="MDD", justification="idk")


def test_max_three_differentials():
    with pytest.raises(ValidationError):
        DiagnosisCreate(primary_dx="MDD", justification="x" * 50,
                        differentials=["a", "b", "c", "d"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diagnosis_schemas.py -v`
Expected: FAIL — `ImportError: cannot import name 'DiagnosisCreate'`.

- [ ] **Step 3: Add the schemas**

In `app/schemas/session.py`, the existing imports already include `BaseModel, Field`, `datetime`, `uuid`. Append these classes at the end of the file:

```python
class DiagnosisCreate(BaseModel):
    primary_dx: str = Field(min_length=1, max_length=255)
    differentials: list[str] = Field(default_factory=list, max_length=3)
    justification: str = Field(min_length=50)


class ScoreOut(BaseModel):
    primary_dx: str
    differentials: list[str]
    justification: str | None
    is_correct: bool | None
    rubric_score: float | None
    response_time_score: float | None
    total_score: float | None
    feedback_text: str | None
    graded_at: datetime | None

    model_config = {"from_attributes": True}


class RevealOut(BaseModel):
    disease_name: str
    dsm_code: str | None
    unit_label: str


class DiagnosisResult(BaseModel):
    correct: bool
    score: ScoreOut | None = None
    reveal: RevealOut | None = None
    hint: str | None = None
```

Then extend the existing `SessionOut` class — add these two fields after `messages`:

```python
class SessionOut(BaseModel):
    id: uuid.UUID
    disease_id: uuid.UUID
    course_id: uuid.UUID
    status: SessionStatus
    turn_count: int
    started_at: datetime
    messages: list[MessageOut]
    score: ScoreOut | None = None
    reveal: RevealOut | None = None

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_diagnosis_schemas.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/schemas/session.py tests/test_diagnosis_schemas.py
git commit -m "feat: add diagnosis request/response schemas"
```

---

## Task 3: LLM gateway — grade_diagnosis + generate_hint

**Files:**
- Modify: `app/services/llm_gateway.py`
- Test: `tests/test_llm_gateway.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_gateway.py` (the `mock_genai` fixture and `_make_disease` helper already exist in this file):

```python
def _make_submission(primary_dx="Major Depressive Disorder",
                     differentials=None, justification="x" * 60):
    from unittest.mock import MagicMock
    s = MagicMock()
    s.primary_dx = primary_dx
    s.differentials = differentials if differentials is not None else ["Bipolar II"]
    s.justification = justification
    return s


@pytest.mark.asyncio
async def test_grade_diagnosis_parses_json(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = (
        '{"is_correct": true, "rubric_score": 88, "feedback": "Solid reasoning."}'
    )
    gw = LLMGateway()

    result = await gw.grade_diagnosis(_make_disease(), _make_submission(), "Patient: hi\nStudent: hello")

    assert result == {"is_correct": True, "rubric_score": 88.0, "feedback": "Solid reasoning."}


@pytest.mark.asyncio
async def test_grade_diagnosis_uses_json_output_and_disables_thinking(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = (
        '{"is_correct": false, "rubric_score": 40, "feedback": "x"}'
    )
    gw = LLMGateway()

    await gw.grade_diagnosis(_make_disease(), _make_submission(), "transcript")

    config = mock_client.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.thinking_config.thinking_budget == 0


@pytest.mark.asyncio
async def test_grade_diagnosis_clamps_rubric_score(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = (
        '{"is_correct": true, "rubric_score": 140, "feedback": "x"}'
    )
    gw = LLMGateway()

    result = await gw.grade_diagnosis(_make_disease(), _make_submission(), "t")
    assert result["rubric_score"] == 100.0


@pytest.mark.asyncio
async def test_grade_diagnosis_empty_raises_502(mock_genai):
    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = ""
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.grade_diagnosis(_make_disease(), _make_submission(), "t")
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_grade_diagnosis_malformed_json_raises_502(mock_genai):
    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = "not json at all"
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.grade_diagnosis(_make_disease(), _make_submission(), "t")
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_generate_hint_returns_text(mock_genai):
    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = "Look more closely at the sleep pattern."
    gw = LLMGateway()

    result = await gw.generate_hint("Generalized Anxiety Disorder", "Major Depressive Disorder")
    assert result == "Look more closely at the sleep pattern."


@pytest.mark.asyncio
async def test_generate_hint_empty_raises_502(mock_genai):
    from fastapi import HTTPException

    from app.services.llm_gateway import LLMGateway

    _, mock_client = mock_genai
    mock_client.models.generate_content.return_value.text = None
    gw = LLMGateway()

    with pytest.raises(HTTPException) as exc:
        await gw.generate_hint("GAD", "MDD")
    assert exc.value.status_code == 502
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_gateway.py -k "grade_diagnosis or generate_hint" -v`
Expected: FAIL — `AttributeError: 'LLMGateway' object has no attribute 'grade_diagnosis'`.

- [ ] **Step 3: Implement the gateway methods**

In `app/services/llm_gateway.py`, add `import json` to the top imports (alongside `asyncio`, `random`). Add a Pydantic schema class and a constant near the top of the module (after `_OPENING_PROMPT`):

```python
import json

from pydantic import BaseModel as _PydBaseModel


class _GradingSchema(_PydBaseModel):
    is_correct: bool
    rubric_score: float
    feedback: str
```

Add these two methods to the `LLMGateway` class (after `generate_patient_message`):

```python
    def _build_grading_prompt(self, disease: Disease, submission, transcript: str) -> str:
        dsm = disease.dsm_code or "no DSM code"
        differentials = ", ".join(submission.differentials) if submission.differentials else "none"
        return (
            "You are a clinical evaluation system. The student was diagnosing a patient "
            f"with {disease.name} ({dsm}).\n\n"
            "The student's diagnosis:\n"
            f"- Primary: {submission.primary_dx}\n"
            f"- Differentials: {differentials}\n"
            f"- Justification: {submission.justification}\n\n"
            f"The conversation transcript:\n{transcript}\n\n"
            "Evaluate:\n"
            "1. Is the primary diagnosis correct? (exact match or clinically equivalent)\n"
            "2. Are any differentials correct?\n"
            "3. Quality of justification (does it reference specific symptoms from the conversation?)\n\n"
            'Respond in JSON: {"is_correct": bool, "rubric_score": 0-100, '
            '"feedback": "specific constructive feedback"}'
        )

    async def grade_diagnosis(self, disease: Disease, submission, transcript: str) -> dict:
        prompt = self._build_grading_prompt(disease, submission, transcript)
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        config = GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=800,
            thinking_config=ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
            response_schema=_GradingSchema,
        )
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model,
            contents=contents,
            config=config,
        )
        if not response.text:
            raise HTTPException(status_code=502, detail="LLM returned empty grading response")
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise HTTPException(
                status_code=502, detail="LLM returned malformed grading response"
            ) from exc
        rubric = max(0.0, min(100.0, float(data.get("rubric_score", 0))))
        return {
            "is_correct": bool(data.get("is_correct", False)),
            "rubric_score": rubric,
            "feedback": str(data.get("feedback", "")),
        }

    async def generate_hint(self, wrong_dx: str, actual_dx: str) -> str:
        prompt = (
            f"The student guessed {wrong_dx}. The actual condition is {actual_dx}. "
            "Give a subtle hint that redirects the student without revealing the answer. "
            f"Do not name {actual_dx} or any obvious synonym. Keep it to one or two sentences."
        )
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        config = GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=200,
            thinking_config=ThinkingConfig(thinking_budget=0),
        )
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model,
            contents=contents,
            config=config,
        )
        if not response.text:
            raise HTTPException(status_code=502, detail="LLM returned empty hint")
        return response.text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm_gateway.py -v`
Expected: PASS (all existing + 7 new tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/llm_gateway.py tests/test_llm_gateway.py
git commit -m "feat: LLM gateway grade_diagnosis + generate_hint"
```

---

## Task 4: Grading service

**Files:**
- Create: `app/services/grading_service.py`
- Test: `tests/test_grading_service.py`

- [ ] **Step 1: Write the failing tests for the pure time-score function**

Create `tests/test_grading_service.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.services.grading_service import compute_response_time_score, grade_diagnosis

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}


def test_time_score_none_is_neutral():
    assert compute_response_time_score(None) == 75.0


def test_time_score_within_grace_is_full():
    assert compute_response_time_score(0) == 100.0
    assert compute_response_time_score(30 * 60) == 100.0


def test_time_score_beyond_floor_is_50():
    assert compute_response_time_score(24 * 60 * 60) == 50.0
    assert compute_response_time_score(48 * 60 * 60) == 50.0


def test_time_score_midpoint_between_50_and_100():
    # 30 min < x < 24 h decays linearly from 100 to 50
    score = compute_response_time_score(12 * 60 * 60)
    assert 50.0 < score < 100.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_grading_service.py -k time_score -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.grading_service'`.

- [ ] **Step 3: Create the grading service with the pure function**

Create `app/services/grading_service.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.score import Score
from app.models.session import Session
from app.services.llm_gateway import gateway
from app.services.session_service import get_session_messages

# Scoring weights — promote to per-course config later.
RUBRIC_WEIGHT = 0.7
TIME_WEIGHT = 0.3

# Response-time scoring, tuned for an asynchronous messaging app.
TIME_SCORE_FULL = 100.0
TIME_SCORE_FLOOR = 50.0
TIME_SCORE_NEUTRAL = 75.0          # no latency data → neutral, not rewarded
GRACE_LATENCY_SEC = 30 * 60        # ≤ 30 min still earns full marks
FLOOR_LATENCY_SEC = 24 * 60 * 60   # ≥ 24 h floored


def compute_response_time_score(avg_latency_sec: float | None) -> float:
    """Floored linear decay from full→floor between the grace and floor windows."""
    if avg_latency_sec is None:
        return TIME_SCORE_NEUTRAL
    if avg_latency_sec <= GRACE_LATENCY_SEC:
        return TIME_SCORE_FULL
    if avg_latency_sec >= FLOOR_LATENCY_SEC:
        return TIME_SCORE_FLOOR
    span = FLOOR_LATENCY_SEC - GRACE_LATENCY_SEC
    frac = (avg_latency_sec - GRACE_LATENCY_SEC) / span
    return TIME_SCORE_FULL - frac * (TIME_SCORE_FULL - TIME_SCORE_FLOOR)


def _avg_student_latency(messages: list[Message]) -> float | None:
    latencies = [
        m.response_latency_sec
        for m in messages
        if m.role == MessageRole.student and m.response_latency_sec is not None
    ]
    if not latencies:
        return None
    return sum(latencies) / len(latencies)


def _build_transcript(messages: list[Message]) -> str:
    lines = []
    for m in messages:
        speaker = "Student" if m.role == MessageRole.student else "Patient"
        lines.append(f"{speaker}: {m.content}")
    return "\n".join(lines)


async def grade_diagnosis(session: Session, submission, db: AsyncSession) -> Score:
    """Build (but do not commit) a Score for the submission. Caller owns the txn."""
    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()
    messages = await get_session_messages(session.id, db)

    avg_latency = _avg_student_latency(messages)
    session.avg_response_latency_sec = avg_latency  # refresh the metric
    transcript = _build_transcript(messages)

    result = await gateway.grade_diagnosis(disease, submission, transcript)
    time_score = compute_response_time_score(avg_latency)
    rubric = result["rubric_score"]
    total = round(RUBRIC_WEIGHT * rubric + TIME_WEIGHT * time_score, 2)

    return Score(
        session_id=session.id,
        primary_dx=submission.primary_dx,
        differentials=list(submission.differentials),
        justification=submission.justification,
        is_correct=result["is_correct"],
        rubric_score=rubric,
        response_time_score=time_score,
        total_score=total,
        feedback_text=result["feedback"],
        graded_at=datetime.now(timezone.utc),
    )


async def generate_diagnosis_hint(wrong_dx: str, actual_dx: str) -> str:
    return await gateway.generate_hint(wrong_dx, actual_dx)
```

- [ ] **Step 4: Run the time-score tests to verify they pass**

Run: `uv run pytest tests/test_grading_service.py -k time_score -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing orchestration tests**

Append to `tests/test_grading_service.py`:

```python
@pytest_asyncio.fixture
async def graded_setup(professor, student, db_session):
    prof, _ = professor
    stu, _ = student
    course = Course(title="P", professor_id=prof.id, class_code="GRD123", is_active=True)
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="Unit 3", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(unit_id=unit.id, name="MDD", category="Mood",
                      key_symptoms=["low mood"], differentials=["GAD"],
                      difficulty_tier=2, speech_style="flat", nudge_behavior=_NUDGE)
    db_session.add(disease)
    await db_session.flush()
    session = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                      started_at=datetime.now(timezone.utc), status=SessionStatus.active)
    db_session.add(session)
    await db_session.flush()
    # patient opening, student reply with a 10-minute latency (within grace → time 100)
    db_session.add(Message(session_id=session.id, role=MessageRole.patient,
                           content="Hi doc.", sent_at=datetime.now(timezone.utc), is_nudge=False))
    db_session.add(Message(session_id=session.id, role=MessageRole.student,
                           content="Tell me more.", sent_at=datetime.now(timezone.utc),
                           is_nudge=False, response_latency_sec=600.0))
    await db_session.commit()
    await db_session.refresh(session)
    return session


def _submission(primary_dx="Major Depressive Disorder"):
    from unittest.mock import MagicMock
    s = MagicMock()
    s.primary_dx = primary_dx
    s.differentials = ["Bipolar II"]
    s.justification = "x" * 60
    return s


async def test_grade_diagnosis_correct_builds_score(graded_setup, db_session):
    with patch("app.services.grading_service.gateway") as gw:
        gw.grade_diagnosis = AsyncMock(return_value={
            "is_correct": True, "rubric_score": 90.0, "feedback": "Great."})
        score = await grade_diagnosis(graded_setup, _submission(), db_session)

    assert score.is_correct is True
    assert score.rubric_score == 90.0
    assert score.response_time_score == 100.0      # 600s within grace window
    assert score.total_score == round(0.7 * 90.0 + 0.3 * 100.0, 2)  # 93.0
    assert score.session_id == graded_setup.id


async def test_grade_diagnosis_incorrect_builds_score(graded_setup, db_session):
    with patch("app.services.grading_service.gateway") as gw:
        gw.grade_diagnosis = AsyncMock(return_value={
            "is_correct": False, "rubric_score": 30.0, "feedback": "Reconsider."})
        score = await grade_diagnosis(graded_setup, _submission("GAD"), db_session)

    assert score.is_correct is False
    assert score.total_score == round(0.7 * 30.0 + 0.3 * 100.0, 2)


async def test_grade_diagnosis_sets_session_avg_latency(graded_setup, db_session):
    with patch("app.services.grading_service.gateway") as gw:
        gw.grade_diagnosis = AsyncMock(return_value={
            "is_correct": True, "rubric_score": 80.0, "feedback": "ok"})
        await grade_diagnosis(graded_setup, _submission(), db_session)
    assert graded_setup.avg_response_latency_sec == 600.0
```

- [ ] **Step 6: Run the orchestration tests to verify they pass**

Run: `uv run pytest tests/test_grading_service.py -v`
Expected: PASS (all 7 tests). The implementation from Step 3 already satisfies them.

- [ ] **Step 7: Commit**

```bash
git add app/services/grading_service.py tests/test_grading_service.py
git commit -m "feat: grading service — time score + diagnosis orchestration"
```

---

## Task 5: POST /sessions/{id}/diagnose endpoint

**Files:**
- Modify: `app/routers/sessions.py`
- Test: `tests/test_sessions_router.py`

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_sessions_router.py` (the `setup` fixture, `_NUDGE`, and imports of `AsyncMock, patch` already exist in this file):

```python
from app.models.score import Score  # add near the other model imports at the top


def _diag_body(primary_dx="GAD"):
    return {"primary_dx": primary_dx,
            "differentials": ["MDD"],
            "justification": "Patient reports persistent worry and restlessness. " + "x" * 20}


async def _seed_active_session(db_session, stu, course, disease):
    session = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                      started_at=datetime.now(timezone.utc), status=SessionStatus.active)
    db_session.add(session)
    await db_session.flush()
    db_session.add(Message(session_id=session.id, role=MessageRole.patient,
                           content="Hi doc.", sent_at=datetime.now(timezone.utc), is_nudge=False))
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def test_diagnose_correct_reveals_and_completes(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup
    session = await _seed_active_session(db_session, stu, course, disease)

    with patch("app.services.grading_service.gateway") as gw:
        gw.grade_diagnosis = AsyncMock(return_value={
            "is_correct": True, "rubric_score": 92.0, "feedback": "Excellent."})
        resp = await client.post(
            f"/api/v1/sessions/{session.id}/diagnose",
            json=_diag_body(primary_dx="GAD"),
            headers={"Authorization": f"Bearer {stu_token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["correct"] is True
    assert data["score"]["total_score"] is not None
    assert data["reveal"]["disease_name"] == "GAD"
    assert data["reveal"]["unit_label"] == "Unit 1"

    # session is now diagnosed and a Score row exists
    from sqlalchemy import select
    refreshed = (await db_session.execute(
        select(Session).where(Session.id == session.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == SessionStatus.diagnosed
    assert refreshed.completed_at is not None
    row = (await db_session.execute(
        select(Score).where(Score.session_id == session.id))).scalar_one_or_none()
    assert row is not None


async def test_diagnose_incorrect_returns_hint_no_score(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup
    session = await _seed_active_session(db_session, stu, course, disease)

    with patch("app.services.grading_service.gateway") as gw:
        gw.grade_diagnosis = AsyncMock(return_value={
            "is_correct": False, "rubric_score": 35.0, "feedback": "Not quite."})
        gw.generate_hint = AsyncMock(return_value="Look again at the worry patterns.")
        resp = await client.post(
            f"/api/v1/sessions/{session.id}/diagnose",
            json=_diag_body(primary_dx="MDD"),
            headers={"Authorization": f"Bearer {stu_token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["correct"] is False
    assert data["hint"] == "Look again at the worry patterns."
    assert data.get("score") is None

    from sqlalchemy import select
    row = (await db_session.execute(
        select(Score).where(Score.session_id == session.id))).scalar_one_or_none()
    assert row is None
    refreshed = (await db_session.execute(
        select(Session).where(Session.id == session.id))).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == SessionStatus.active


async def test_diagnose_session_not_active_returns_409(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup
    session = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                      started_at=datetime.now(timezone.utc), status=SessionStatus.diagnosed)
    db_session.add(session)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/sessions/{session.id}/diagnose",
        json=_diag_body(),
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 409


async def test_diagnose_not_owner_returns_404(client, setup, db_session, rsa_keys):
    _, _, stu, _, course, disease = setup
    private_pem, _ = rsa_keys
    other = User(google_uid=f"dx-{uuid.uuid4().hex}",
                 email=f"dx-{uuid.uuid4().hex[:8]}@test.edu",
                 role=UserRole.student, is_verified=True, display_name="Other",
                 created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    db_session.add(other)
    session = await _seed_active_session(db_session, stu, course, disease)

    from jose import jwt
    token = jwt.encode({"sub": str(other.id)}, private_pem, algorithm="RS256")
    resp = await client.post(
        f"/api/v1/sessions/{session.id}/diagnose",
        json=_diag_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_diagnose_professor_forbidden(client, setup, db_session):
    _, prof_token, stu, _, course, disease = setup
    session = await _seed_active_session(db_session, stu, course, disease)

    resp = await client.post(
        f"/api/v1/sessions/{session.id}/diagnose",
        json=_diag_body(),
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 403


async def test_diagnose_short_justification_returns_422(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup
    session = await _seed_active_session(db_session, stu, course, disease)

    resp = await client.post(
        f"/api/v1/sessions/{session.id}/diagnose",
        json={"primary_dx": "GAD", "differentials": [], "justification": "idk"},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_router.py -k diagnose -v`
Expected: FAIL — 404/405 (route not defined) for the success cases.

- [ ] **Step 3: Implement the endpoint**

In `app/routers/sessions.py`, update imports. Add to the model imports:

```python
from app.models.disease import Disease
from app.models.score import Score
from app.models.unit import Unit
```

Update the schema import line to include the new schemas:

```python
from app.schemas.session import (
    DiagnosisCreate,
    DiagnosisResult,
    MessageCreate,
    MessageOut,
    RevealOut,
    ScoreOut,
    SessionCreate,
    SessionOut,
)
```

Add the grading-service import alongside the existing `session_service` import:

```python
from app.services.grading_service import generate_diagnosis_hint, grade_diagnosis
```

Add `datetime` for `completed_at`:

```python
from datetime import datetime, timezone
```

Add the endpoint (place it after `send_message`, before `create_session`):

```python
@router.post("/{session_id}/diagnose", response_model=DiagnosisResult)
async def diagnose(
    session_id: uuid.UUID,
    body: DiagnosisCreate,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
) -> DiagnosisResult:
    session = (
        await db.execute(
            select(Session).where(
                Session.id == session_id,
                Session.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.active:
        raise HTTPException(status_code=409, detail="Session is not active")

    score = await grade_diagnosis(session, body, db)
    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()

    if not score.is_correct:
        hint = await generate_diagnosis_hint(body.primary_dx, disease.name)
        # Persist the refreshed avg latency; session stays active, no score row.
        await db.commit()
        return DiagnosisResult(correct=False, hint=hint)

    db.add(score)
    session.status = SessionStatus.diagnosed
    session.completed_at = datetime.now(timezone.utc)
    db.add(session)
    await db.commit()
    await db.refresh(score)

    unit = (
        await db.execute(select(Unit).where(Unit.id == disease.unit_id))
    ).scalar_one()
    return DiagnosisResult(
        correct=True,
        score=ScoreOut.model_validate(score),
        reveal=RevealOut(
            disease_name=disease.name,
            dsm_code=disease.dsm_code,
            unit_label=unit.label,
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_router.py -k diagnose -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add app/routers/sessions.py tests/test_sessions_router.py
git commit -m "feat: POST /sessions/{id}/diagnose — grade, reveal, or hint"
```

---

## Task 6: Reveal on GET /sessions/{id}

**Files:**
- Modify: `app/routers/sessions.py`
- Test: `tests/test_sessions_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sessions_router.py`:

```python
async def test_get_session_diagnosed_includes_reveal(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup
    session = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                      started_at=datetime.now(timezone.utc),
                      status=SessionStatus.diagnosed,
                      completed_at=datetime.now(timezone.utc))
    db_session.add(session)
    await db_session.flush()
    db_session.add(Score(session_id=session.id, primary_dx="GAD", differentials=["MDD"],
                         justification="x" * 60, is_correct=True, rubric_score=90.0,
                         response_time_score=100.0, total_score=93.0,
                         feedback_text="Great.", graded_at=datetime.now(timezone.utc)))
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["score"]["total_score"] == 93.0
    assert data["reveal"]["disease_name"] == "GAD"
    assert data["reveal"]["unit_label"] == "Unit 1"


async def test_get_session_active_hides_reveal(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup
    session = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                      started_at=datetime.now(timezone.utc), status=SessionStatus.active)
    db_session.add(session)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["score"] is None
    assert data["reveal"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_router.py -k "reveal" -v`
Expected: FAIL — `test_get_session_diagnosed_includes_reveal` fails because `data["reveal"]` is `None` (reveal not yet built).

- [ ] **Step 3: Implement the reveal helper and wire it into GET**

In `app/routers/sessions.py`, update `_session_out` to accept the optional reveal data:

```python
def _session_out(
    session: Session,
    messages: list[Message],
    score: ScoreOut | None = None,
    reveal: RevealOut | None = None,
) -> SessionOut:
    return SessionOut(
        id=session.id,
        disease_id=session.disease_id,
        course_id=session.course_id,
        status=session.status,
        turn_count=session.turn_count,
        started_at=session.started_at,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                sent_at=m.sent_at,
                response_latency_sec=m.response_latency_sec,
            )
            for m in messages
        ],
        score=score,
        reveal=reveal,
    )
```

Add a reveal-loader helper just below `_session_out`:

```python
async def _load_reveal(
    session: Session, db: AsyncSession
) -> tuple[ScoreOut | None, RevealOut | None]:
    if session.status != SessionStatus.diagnosed:
        return None, None
    score_row = (
        await db.execute(select(Score).where(Score.session_id == session.id))
    ).scalar_one_or_none()
    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()
    unit = (
        await db.execute(select(Unit).where(Unit.id == disease.unit_id))
    ).scalar_one()
    score_out = ScoreOut.model_validate(score_row) if score_row is not None else None
    reveal = RevealOut(
        disease_name=disease.name,
        dsm_code=disease.dsm_code,
        unit_label=unit.label,
    )
    return score_out, reveal
```

In the `get_session` endpoint, replace the final two lines:

```python
    messages = await get_session_messages(session.id, db)
    return _session_out(session, messages)
```

with:

```python
    messages = await get_session_messages(session.id, db)
    score_out, reveal = await _load_reveal(session, db)
    return _session_out(session, messages, score_out, reveal)
```

(Leave `get_active_session_endpoint` unchanged — it only ever returns active sessions, which have no reveal.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_router.py -k "reveal" -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full sessions test file**

Run: `uv run pytest tests/test_sessions_router.py -v`
Expected: PASS (all existing + new tests — confirms the `_session_out` signature change didn't break the active/by-id/message tests).

- [ ] **Step 6: Commit**

```bash
git add app/routers/sessions.py tests/test_sessions_router.py
git commit -m "feat: reveal score + disease on GET /sessions/{id} when diagnosed"
```

---

## Task 7: API contract docs

**Files:**
- Modify: `docs/api-contract.md`

- [ ] **Step 1: Find the Sessions section**

Run: `grep -n "sessions" docs/api-contract.md`
Locate the Sessions table (the rows for `POST /sessions`, `GET /sessions/active`, `GET /sessions/{id}`, `POST /sessions/{id}/messages`).

- [ ] **Step 2: Add the diagnose row to the Sessions table**

Add this row to the Sessions endpoint table:

```markdown
| POST | `/api/v1/sessions/{id}/diagnose` | Submit a diagnosis; grade or hint | Bearer JWT (student owner) | ✅ Week 8 |
```

- [ ] **Step 3: Add the endpoint detail block**

After the existing `POST /sessions/{id}/messages` detail block, add:

```markdown
### POST /api/v1/sessions/{session_id}/diagnose
**Role required:** student (session owner)
**Request:** `{"primary_dx": "Major Depressive Disorder", "differentials": ["Bipolar II", "Adjustment Disorder"], "justification": "Patient presents with... (min 50 chars)"}`
**Response (correct):** `{"correct": true, "score": ScoreOut, "reveal": {"disease_name": "...", "dsm_code": "...", "unit_label": "..."}}` — session becomes `diagnosed`, `completed_at` set.
**Response (incorrect):** `{"correct": false, "hint": "Consider re-examining the patient's speech patterns"}` — session stays `active`, nothing persisted.
**Errors:** 403 not a student, 404 session not found / not owner, 409 session not active, 422 invalid body (empty primary_dx, >3 differentials, justification <50 chars), 502 LLM failure

`ScoreOut` = primary_dx, differentials, justification, is_correct, rubric_score, response_time_score, total_score, feedback_text, graded_at.

**Note:** `GET /api/v1/sessions/{id}` now includes `score` and `reveal` (both `ScoreOut`/reveal objects) for `diagnosed` sessions, and `null` for both while `active`.
```

- [ ] **Step 4: Commit**

```bash
git add docs/api-contract.md
git commit -m "docs: document diagnose endpoint + reveal on GET session"
```

---

## Task 8: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `uv run pytest -v`
Expected: all tests PASS. If `scores`-related failures appear about a missing table in the *dev* DB (not test DB), confirm Task 1 Step 8 (`alembic upgrade head`) ran.

- [ ] **Step 2: Sanity-check the app imports**

Run: `uv run python -c "from app.main import app; print('ok')"`
Expected: `ok` (no import errors from the new modules/routes).

- [ ] **Step 3: Final commit if anything was touched**

Only if Steps 1–2 required fixes:

```bash
git add -A
git commit -m "test: Week 8 full-suite green"
```

---

## Self-Review Notes

- **Spec coverage:** Task 1 → scores table; Task 2 → schemas (incl. `justification min_length=50`); Tasks 3–4 → grading engine (Gemini JSON grade + hint, async-tuned time score, 0.7/0.3 weights); Task 5 → diagnose endpoint (correct persists+reveals+completes, incorrect hint-only no row, 409/404/403/422); Task 6 → reveal on GET (only when diagnosed); Task 7 → docs. All spec sections mapped.
- **Persistence rule honored:** incorrect path calls `db.commit()` only to save the refreshed `avg_response_latency_sec`; the `Score` object is never `db.add`-ed, so no row is written.
- **Mock points:** service/router tests patch `app.services.grading_service.gateway` (covers both `grade_diagnosis` and `generate_diagnosis_hint`); gateway unit tests patch `app.services.llm_gateway.genai`.
- **Type consistency:** `grade_diagnosis(session, submission, db) -> Score` (uncommitted); router owns the txn; `compute_response_time_score(float|None) -> float`; gateway `grade_diagnosis(disease, submission, transcript) -> dict` with keys `is_correct/rubric_score/feedback`.
