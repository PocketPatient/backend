# Week 11 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the full transcript to the LLM with token budgeting, keep the AI patient in character via a regenerate/fallback guardrail, and add a `get_session_stats` analytics helper.

**Architecture:** Three independent service units — `context_window.py` (pure token counting + sliding window), `character_guardrail.py` (pure break-check + a DB-aware retry/fallback orchestrator), and `analytics_service.py` (read-only aggregation). The LLM gateway methods keep their `-> str` signatures and stay DB-free; the guardrail orchestrator is invoked by the existing callers (`bot_reply`, `create_new_session`) that already hold a DB session.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 async, Pydantic v2, pytest/pytest-asyncio, `tiktoken`. Run everything with `uv run`.

**Note:** No DB migration is required — `Message.token_count` and the `'system'` value of the `message_role` enum already exist (migration `b7d991975e12`).

---

## File Structure

- Create: `app/services/context_window.py` — `count_tokens`, `build_history` (Task 2)
- Create: `app/services/character_guardrail.py` — `check_character_break`, `generate_in_character` (Task 4)
- Create: `app/services/analytics_service.py` — `get_session_stats` (Task 7)
- Modify: `app/schemas/session.py` — add `SessionStats` (Task 7)
- Modify: `app/services/session_service.py` — token_count on student + opening msgs, guardrail wiring (Tasks 3, 6)
- Modify: `app/tasks/bot_reply.py` — `build_history`, token_count, guardrail wiring (Tasks 3, 5)
- Modify: `app/tasks/nudge.py` — token_count on nudge msg (Task 3)
- Create: `tests/test_context_window.py`, `tests/test_character_guardrail.py`, `tests/test_analytics_service.py`
- Modify: `tests/test_bot_reply.py`, `tests/test_session_service.py`

---

## Task 1: Add tiktoken dependency

**Files:**
- Modify: `pyproject.toml` (via `uv add`)

- [ ] **Step 1: Add the dependency**

Run: `uv add tiktoken`
Expected: `tiktoken` appears under `[project.dependencies]` and `uv.lock` updates.

- [ ] **Step 2: Verify it imports**

Run: `uv run python -c "import tiktoken; print(len(tiktoken.get_encoding('cl100k_base').encode('hello world')))"`
Expected: prints `2`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add tiktoken for context token counting"
```

---

## Task 2: context_window — count_tokens + build_history

**Files:**
- Create: `app/services/context_window.py`
- Test: `tests/test_context_window.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_window.py
from __future__ import annotations

import uuid

from app.models.message import Message, MessageRole
from app.services.context_window import (
    MAX_CONTEXT_TOKENS,
    OMITTED_NOTE,
    build_history,
    count_tokens,
)


def _msg(role: MessageRole, content: str, token_count: int | None = None) -> Message:
    return Message(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role=role,
        content=content,
        token_count=token_count,
    )


def test_count_tokens_is_positive_and_monotonic():
    assert count_tokens("hello") >= 1
    assert count_tokens("hello world foo bar") > count_tokens("hello")


def test_build_history_maps_roles_and_filters_system():
    messages = [
        _msg(MessageRole.patient, "Hi doctor", token_count=2),
        _msg(MessageRole.student, "How are you?", token_count=3),
        _msg(MessageRole.system, "[regenerated: ai_break]", token_count=None),
    ]
    history = build_history(messages)
    assert history == [
        {"role": "model", "parts": [{"text": "Hi doctor"}]},
        {"role": "user", "parts": [{"text": "How are you?"}]},
    ]


def test_build_history_windows_when_over_limit():
    # 8 messages each well over the limit so the window must drop the middle.
    big = "word " * (MAX_CONTEXT_TOKENS // 4)
    messages = [_msg(MessageRole.student, f"{i} {big}") for i in range(8)]
    history = build_history(messages)
    texts = [c["parts"][0]["text"] for c in history]
    # First 5 kept, omitted note inserted, at least the newest tail message kept.
    assert texts[0].startswith("0 ")
    assert texts[4].startswith("4 ")
    assert OMITTED_NOTE in texts
    assert texts[-1].startswith("7 ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_context_window.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.context_window'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/context_window.py
from __future__ import annotations

import logging

import tiktoken

from app.models.message import Message, MessageRole

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 100_000
WARN_CONTEXT_TOKENS = 50_000
HEAD_KEEP = 5
OMITTED_NOTE = "[Earlier messages omitted for length]"

_encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding.encode(text))


def _to_content(message: Message) -> dict:
    role = "user" if message.role == MessageRole.student else "model"
    return {"role": role, "parts": [{"text": message.content}]}


def _message_tokens(message: Message) -> int:
    if message.token_count is not None:
        return message.token_count
    return count_tokens(message.content)


def build_history(messages: list[Message]) -> list[dict]:
    """Assemble the Gemini `contents` list from the transcript.

    Filters internal `system` notes, logs a warning past WARN_CONTEXT_TOKENS, and
    applies a first-HEAD_KEEP + trailing sliding window past MAX_CONTEXT_TOKENS.
    """
    visible = [m for m in messages if m.role != MessageRole.system]
    total = sum(_message_tokens(m) for m in visible)

    if total > WARN_CONTEXT_TOKENS:
        session_id = visible[0].session_id if visible else None
        logger.warning(
            "Session %s context is %d tokens (warn threshold %d)",
            session_id,
            total,
            WARN_CONTEXT_TOKENS,
        )

    if total <= MAX_CONTEXT_TOKENS:
        return [_to_content(m) for m in visible]

    head = visible[:HEAD_KEEP]
    budget = MAX_CONTEXT_TOKENS - sum(_message_tokens(m) for m in head)
    tail: list[Message] = []
    for m in reversed(visible[HEAD_KEEP:]):
        t = _message_tokens(m)
        if t > budget:
            break
        tail.insert(0, m)
        budget -= t

    contents = [_to_content(m) for m in head]
    contents.append({"role": "user", "parts": [{"text": OMITTED_NOTE}]})
    contents.extend(_to_content(m) for m in tail)
    return contents
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_context_window.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/context_window.py tests/test_context_window.py
git commit -m "feat: context_window token counting and sliding window"
```

---

## Task 3: Populate token_count on every Message creation

**Files:**
- Modify: `app/services/session_service.py` (student msg ~line 164, opening msg ~line 97)
- Modify: `app/tasks/bot_reply.py` (patient reply ~line 50)
- Modify: `app/tasks/nudge.py` (nudge msg ~line 102)
- Test: `tests/test_session_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_session_service.py`:

```python
async def test_opening_message_gets_token_count(db_session, setup):
    _, stu, course, disease = setup

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_opening_message = AsyncMock(return_value="Hi, I need some help.")
        from app.services.session_service import create_new_session
        _, message = await create_new_session(stu.id, course.id, db_session)

    assert message.token_count is not None
    assert message.token_count > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_service.py::test_opening_message_gets_token_count -v`
Expected: FAIL — `assert None is not None`

- [ ] **Step 3: Add token counting at all four creation sites**

In `app/services/session_service.py`, add the import near the top:

```python
from app.services.context_window import count_tokens
```

Opening message (`create_new_session`), add `token_count`:

```python
    message = Message(
        session_id=session.id,
        role=MessageRole.patient,
        content=opening_text,
        sent_at=now,
        is_nudge=False,
        token_count=count_tokens(opening_text),
    )
```

Student message (`handle_student_message`), add `token_count`:

```python
    student_msg = Message(
        session_id=session.id,
        role=MessageRole.student,
        content=student_content,
        sent_at=now,
        is_nudge=False,
        response_latency_sec=latency,
        token_count=count_tokens(student_content),
    )
```

In `app/tasks/bot_reply.py`, add the import:

```python
from app.services.context_window import count_tokens
```

And the patient reply Message:

```python
        db.add(Message(
            session_id=session.id,
            role=MessageRole.patient,
            content=reply_text,
            sent_at=datetime.now(timezone.utc),
            is_nudge=False,
            token_count=count_tokens(reply_text),
        ))
```

In `app/tasks/nudge.py`, add the import:

```python
from app.services.context_window import count_tokens
```

And the nudge Message:

```python
    db.add(Message(
        session_id=session.id,
        role=MessageRole.patient,
        content=text,
        sent_at=now,
        is_nudge=True,
        token_count=count_tokens(text),
    ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_service.py tests/test_bot_reply.py tests/test_nudge.py -v`
Expected: PASS (including the new `test_opening_message_gets_token_count`)

- [ ] **Step 5: Commit**

```bash
git add app/services/session_service.py app/tasks/bot_reply.py app/tasks/nudge.py tests/test_session_service.py
git commit -m "feat: store token_count on every message"
```

---

## Task 4: character_guardrail — break check + retry/fallback orchestrator

**Files:**
- Create: `app/services/character_guardrail.py`
- Test: `tests/test_character_guardrail.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_character_guardrail.py
from __future__ import annotations

import uuid

import pytest

from app.models.message import MessageRole
from app.services.character_guardrail import (
    FALLBACK_TEXT,
    check_character_break,
    generate_in_character,
)


class _FakeDB:
    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)


def test_check_detects_ai_break():
    assert check_character_break("Well, as an AI I can't feel that.", "MDD") == "ai_break"


def test_check_detects_diagnosis_leak():
    assert check_character_break("I think I have MDD, doctor.", "MDD") == "diagnosis_leak"


def test_check_passes_clean_text():
    assert check_character_break("I just feel so tired and sad lately.", "MDD") is None


async def test_generate_in_character_returns_clean_first_try():
    db = _FakeDB()

    async def gen():
        return "I can't sleep and I feel hopeless."

    out = await generate_in_character(
        gen, disease_name="MDD", db=db, session_id=uuid.uuid4()
    )
    assert out == "I can't sleep and I feel hopeless."
    assert db.added == []


async def test_generate_in_character_regenerates_then_succeeds():
    db = _FakeDB()
    replies = iter(["As an AI, I cannot help.", "I just feel awful, doctor."])

    async def gen():
        return next(replies)

    out = await generate_in_character(
        gen, disease_name="MDD", db=db, session_id=uuid.uuid4()
    )
    assert out == "I just feel awful, doctor."
    assert len(db.added) == 1
    assert db.added[0].role == MessageRole.system
    assert "regenerated" in db.added[0].content


async def test_generate_in_character_falls_back_after_max_retries():
    db = _FakeDB()

    async def gen():
        return "I have MDD."  # always leaks

    out = await generate_in_character(
        gen, disease_name="MDD", db=db, session_id=uuid.uuid4()
    )
    assert out == FALLBACK_TEXT
    # 2 regenerated rows + 1 fallback row
    assert len(db.added) == 3
    assert "fallback used" in db.added[-1].content
    assert all(m.role == MessageRole.system for m in db.added)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_character_guardrail.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.character_guardrail'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/character_guardrail.py
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, MessageRole

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
FALLBACK_TEXT = "I don't know, doctor... I just don't feel right"
AI_BREAK_PHRASES = [
    "as an ai",
    "as a language model",
    "i'm a language model",
    "i am a language model",
    "i'm an ai",
    "i am an ai",
    "i don't actually have",
]


def check_character_break(text: str, disease_name: str) -> str | None:
    """Return a violation reason ('ai_break' / 'diagnosis_leak') or None if in character."""
    lowered = text.lower()
    if any(phrase in lowered for phrase in AI_BREAK_PHRASES):
        return "ai_break"
    if disease_name and disease_name.lower() in lowered:
        return "diagnosis_leak"
    return None


def _system_message(session_id: uuid.UUID, content: str) -> Message:
    return Message(
        session_id=session_id,
        role=MessageRole.system,
        content=content,
        sent_at=datetime.now(timezone.utc),
        is_nudge=False,
        token_count=None,
    )


async def generate_in_character(
    generate_fn: Callable[[], Awaitable[str]],
    *,
    disease_name: str,
    db: AsyncSession,
    session_id: uuid.UUID,
) -> str:
    """Generate patient text, regenerating up to MAX_RETRIES on a character break,
    then fall back to a generic in-character line. Logs each regeneration/fallback as
    an internal `system` Message (db.add only — caller commits)."""
    reason: str | None = None
    for attempt in range(MAX_RETRIES + 1):
        text = await generate_fn()
        reason = check_character_break(text, disease_name)
        if reason is None:
            return text
        if attempt < MAX_RETRIES:
            logger.warning(
                "Character break (%s) on session %s attempt %d; regenerating",
                reason,
                session_id,
                attempt,
            )
            db.add(_system_message(session_id, f"[regenerated: {reason}]"))
    logger.warning(
        "Character guardrail fell back on session %s after %d retries (last reason: %s)",
        session_id,
        MAX_RETRIES,
        reason,
    )
    db.add(_system_message(session_id, f"[fallback used: {reason}]"))
    return FALLBACK_TEXT
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_character_guardrail.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/character_guardrail.py tests/test_character_guardrail.py
git commit -m "feat: character consistency guardrail with regenerate/fallback"
```

---

## Task 5: Wire build_history + guardrail into bot_reply

**Files:**
- Modify: `app/tasks/bot_reply.py:38-48`
- Test: `tests/test_bot_reply.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bot_reply.py` (follow the existing mock-gateway pattern in that file; the test below assumes the helper `_setup_session` style already used there — adapt variable names to the file's existing fixtures):

```python
async def test_bot_reply_regenerates_on_character_break(db_session):
    # Build an active session with one patient + one student message.
    from tests.test_bot_reply import _make_active_session  # reuse existing helper if present
    session = await _make_active_session(db_session, speech_style="flat")

    from unittest.mock import AsyncMock, patch
    from app.models.message import Message, MessageRole
    from sqlalchemy import select

    replies = iter(["As an AI I cannot do that.", "I just feel numb, doctor."])

    async def _side_effect(*args, **kwargs):
        return next(replies)

    with patch("app.tasks.bot_reply.gateway") as mock_gw:
        mock_gw.generate_patient_message = AsyncMock(side_effect=_side_effect)
        from app.tasks.bot_reply import _generate_and_send
        await _generate_and_send(str(session.id), session.pending_reply_task_id)

    rows = (await db_session.execute(
        select(Message).where(Message.session_id == session.id)
    )).scalars().all()
    system_rows = [m for m in rows if m.role == MessageRole.system]
    patient_replies = [m for m in rows if m.role == MessageRole.patient and not m.is_nudge]
    assert any("regenerated" in m.content for m in system_rows)
    assert patient_replies[-1].content == "I just feel numb, doctor."
```

> If `tests/test_bot_reply.py` has no reusable session-builder helper, construct the session/disease/messages inline using the model imports already present in that file (mirror `tests/test_session_service.py`'s `setup` fixture), and set `session.pending_reply_task_id` to a known uuid string before calling `_generate_and_send`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot_reply.py::test_bot_reply_regenerates_on_character_break -v`
Expected: FAIL — currently `generate_patient_message` is called once (no retry), so no system row and the reply equals the first (broken) text.

- [ ] **Step 3: Update bot_reply to use build_history + generate_in_character**

In `app/tasks/bot_reply.py`, add imports:

```python
from app.services.character_guardrail import generate_in_character
from app.services.context_window import build_history, count_tokens
```

Replace the history-building block and the generate call (current lines ~38-48):

```python
        messages = await get_session_messages(session.id, db)
        history = build_history(messages)

        patient_name, patient_age = patient_identity(session.id.int)
        reply_text = await generate_in_character(
            lambda: gateway.generate_patient_message(
                disease, patient_name, patient_age, history
            ),
            disease_name=disease.name,
            db=db,
            session_id=session.id,
        )
```

(The `MessageRole` import is still used elsewhere; keep it. `count_tokens` was added in Task 3 — keep that import.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bot_reply.py -v`
Expected: PASS (existing tests + the new regeneration test)

- [ ] **Step 5: Commit**

```bash
git add app/tasks/bot_reply.py tests/test_bot_reply.py
git commit -m "feat: bot_reply uses build_history and character guardrail"
```

---

## Task 6: Wire guardrail into create_new_session (opening message)

**Files:**
- Modify: `app/services/session_service.py:93-94`
- Test: `tests/test_session_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_session_service.py`:

```python
async def test_opening_message_falls_back_on_persistent_break(db_session, setup):
    _, stu, course, disease = setup  # disease.name == "GAD"

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_opening_message = AsyncMock(return_value="As an AI, I greet you.")
        from app.services.session_service import create_new_session
        from app.services.character_guardrail import FALLBACK_TEXT
        session, message = await create_new_session(stu.id, course.id, db_session)

    assert message.content == FALLBACK_TEXT

    from sqlalchemy import select
    from app.models.message import Message, MessageRole
    system_rows = (await db_session.execute(
        select(Message).where(
            Message.session_id == session.id,
            Message.role == MessageRole.system,
        )
    )).scalars().all()
    assert any("fallback used" in m.content for m in system_rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_service.py::test_opening_message_falls_back_on_persistent_break -v`
Expected: FAIL — opening text is stored verbatim ("As an AI, I greet you."), no fallback, no system row.

- [ ] **Step 3: Wrap the opening generation with the guardrail**

In `app/services/session_service.py`, add the import:

```python
from app.services.character_guardrail import generate_in_character
```

Replace the opening generation (current lines ~93-94):

```python
    patient_name, patient_age = patient_identity(session.id.int)
    opening_text = await generate_in_character(
        lambda: gateway.generate_opening_message(disease, patient_name, patient_age),
        disease_name=disease.name,
        db=db,
        session_id=session.id,
    )
```

(`session.id` is available — `await db.flush()` runs just above.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_service.py -v`
Expected: PASS (existing tests still green — their mock returns clean text; new fallback test passes)

- [ ] **Step 5: Commit**

```bash
git add app/services/session_service.py tests/test_session_service.py
git commit -m "feat: opening message routed through character guardrail"
```

---

## Task 7: Conversation analytics — get_session_stats

**Files:**
- Modify: `app/schemas/session.py` (add `SessionStats`)
- Create: `app/services/analytics_service.py`
- Test: `tests/test_analytics_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analytics_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole

pytestmark = pytest.mark.usefixtures("clean_tables")


@pytest_asyncio.fixture
async def stats_setup(db_session):
    prof = User(google_uid="an-prof", email="an-prof@test.edu", role=UserRole.professor, is_verified=True)
    stu = User(google_uid="an-stu", email="an-stu@test.edu", role=UserRole.student, is_verified=True)
    db_session.add_all([prof, stu])
    await db_session.flush()

    course = Course(title="Psych", professor_id=prof.id, class_code="ANS123")
    db_session.add(course)
    await db_session.flush()

    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released, release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id, name="GAD", category="Anxiety",
        key_symptoms=["worry", "restlessness", "fatigue"], differentials=["MDD"],
        difficulty_tier=2, speech_style="anxious", nudge_behavior={},
    )
    db_session.add(disease)
    await db_session.flush()

    started = datetime.now(timezone.utc) - timedelta(minutes=10)
    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=started, completed_at=started + timedelta(minutes=10),
        status=SessionStatus.diagnosed, turn_count=2,
        avg_response_latency_sec=42.0,
    )
    db_session.add(session)
    await db_session.flush()

    db_session.add_all([
        Message(session_id=session.id, role=MessageRole.patient, content="Hi", sent_at=started),
        Message(session_id=session.id, role=MessageRole.student,
                content="Do you have a lot of worry and restlessness?", sent_at=started + timedelta(minutes=1)),
        Message(session_id=session.id, role=MessageRole.student,
                content="Tell me more.", sent_at=started + timedelta(minutes=2)),
    ])
    await db_session.commit()
    return session, disease


async def test_get_session_stats(db_session, stats_setup):
    session, _ = stats_setup
    from app.services.analytics_service import get_session_stats

    stats = await get_session_stats(session.id, db_session)

    assert stats.total_turns == 2
    assert stats.total_duration_sec == pytest.approx(600, abs=2)
    assert stats.avg_response_latency_sec == 42.0
    # two student messages: lengths 44 and 13 (compute via len in assertions loosely)
    assert stats.student_msg_len_max >= stats.student_msg_len_min
    assert stats.student_msg_len_avg is not None
    # "worry" and "restlessness" covered, "fatigue" missed -> 2/3
    assert stats.topic_coverage_score == pytest.approx(2 / 3)
    assert set(stats.topics_covered) == {"worry", "restlessness"}
    assert stats.topics_missed == ["fatigue"]


async def test_get_session_stats_missing_raises():
    import uuid
    from app.services.analytics_service import get_session_stats
    # Use a fresh db session via fixture is overkill; rely on the stats_setup db in practice.
    with pytest.raises(ValueError):
        # session_id that does not exist
        await get_session_stats(uuid.uuid4(), _DB_PLACEHOLDER)  # see note below
```

> For `test_get_session_stats_missing_raises`, request the `db_session` fixture and pass it instead of `_DB_PLACEHOLDER` (drop the placeholder line). Final form:
> ```python
> async def test_get_session_stats_missing_raises(db_session):
>     import uuid
>     from app.services.analytics_service import get_session_stats
>     with pytest.raises(ValueError):
>         await get_session_stats(uuid.uuid4(), db_session)
> ```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analytics_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.analytics_service'`

- [ ] **Step 3: Add the SessionStats schema**

Append to `app/schemas/session.py`:

```python
class SessionStats(BaseModel):
    total_turns: int
    total_duration_sec: float | None
    avg_response_latency_sec: float | None
    student_msg_len_avg: float | None
    student_msg_len_min: int | None
    student_msg_len_max: int | None
    topic_coverage_score: float
    topics_covered: list[str]
    topics_missed: list[str]
```

- [ ] **Step 4: Implement get_session_stats**

```python
# app/services/analytics_service.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.schemas.session import SessionStats


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def get_session_stats(session_id: uuid.UUID, db: AsyncSession) -> SessionStats:
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()

    student_msgs = list(
        (
            await db.execute(
                select(Message).where(
                    Message.session_id == session_id,
                    Message.role == MessageRole.student,
                )
            )
        ).scalars().all()
    )

    end = session.completed_at or datetime.now(timezone.utc)
    duration = (_aware(end) - _aware(session.started_at)).total_seconds()

    lengths = [len(m.content) for m in student_msgs]
    if lengths:
        len_avg: float | None = sum(lengths) / len(lengths)
        len_min: int | None = min(lengths)
        len_max: int | None = max(lengths)
    else:
        len_avg = len_min = len_max = None

    student_text = " ".join(m.content for m in student_msgs).lower()
    symptoms = disease.key_symptoms or []
    covered = [s for s in symptoms if s.lower() in student_text]
    missed = [s for s in symptoms if s.lower() not in student_text]
    score = len(covered) / len(symptoms) if symptoms else 0.0

    return SessionStats(
        total_turns=session.turn_count,
        total_duration_sec=duration,
        avg_response_latency_sec=session.avg_response_latency_sec,
        student_msg_len_avg=len_avg,
        student_msg_len_min=len_min,
        student_msg_len_max=len_max,
        topic_coverage_score=score,
        topics_covered=covered,
        topics_missed=missed,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_analytics_service.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add app/schemas/session.py app/services/analytics_service.py tests/test_analytics_service.py
git commit -m "feat: get_session_stats conversation analytics helper"
```

---

## Task 8: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -v`
Expected: all tests PASS, no errors.

- [ ] **Step 2: If anything fails, debug with the systematic-debugging skill, fix, and re-run before proceeding.**

---

## Self-Review Notes (author)

- **Spec coverage:** Task 1 dep + Task 2 = context mgmt (token_count, 100K window, 50K warn); Task 3 = token_count population; Task 4 + 5 + 6 = guardrail (checks, retry, fallback, system-message logging) on replies + opening; Task 7 = analytics (turns, duration, latency, msg lengths, topic coverage). Migration check resolved (none needed). All spec sections mapped.
- **Type consistency:** `count_tokens`, `build_history`, `check_character_break`, `generate_in_character(generate_fn, *, disease_name, db, session_id)`, `get_session_stats(session_id, db) -> SessionStats` used identically across tasks.
- **Known v1 limitation (from spec):** diagnosis-leak is a plain substring match on `disease.name`; short names risk false positives — acceptable for now.
