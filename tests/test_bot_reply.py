from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}


@pytest_asyncio.fixture
async def br_setup(professor, student, db_session):
    prof, _ = professor
    stu, _ = student
    course = Course(title="Bot Reply Course", professor_id=prof.id, class_code="BRC001")
    db_session.add(course)
    await db_session.flush()

    unit = Unit(
        course_id=course.id, label="Unit 1",
        status=UnitStatus.released, release_date=datetime.now(timezone.utc),
    )
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id, name="MDD", category="Mood",
        key_symptoms=["low mood"], differentials=["GAD"],
        difficulty_tier=2, speech_style="flat", nudge_behavior=_NUDGE,
    )
    db_session.add(disease)
    await db_session.flush()

    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(stu)
    return stu, course, disease


def _ctx(db_session):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


async def _make_session(db_session, stu, course, disease, *, task_id, status=SessionStatus.active):
    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=status, turn_count=0,
        pending_reply_task_id=task_id,
    )
    db_session.add(session)
    await db_session.flush()
    db_session.add(Message(
        session_id=session.id, role=MessageRole.student, content="I've been feeling down.",
        sent_at=datetime.now(timezone.utc), is_nudge=False,
    ))
    await db_session.commit()
    await db_session.refresh(session)
    return session


# --- _generate_and_send (async helper) ---

async def test_generate_and_send_skips_when_superseded_before_llm(br_setup, db_session):
    from app.tasks.bot_reply import _generate_and_send

    stu, course, disease = br_setup
    session = await _make_session(db_session, stu, course, disease, task_id="newer-task")

    with patch("app.tasks.bot_reply.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.bot_reply.gateway") as mock_gw, \
         patch("app.tasks.bot_reply.send_push") as mock_push:
        await _generate_and_send(str(session.id), "stale-task")

    mock_gw.generate_patient_message.assert_not_called()
    mock_push.delay.assert_not_called()


async def test_generate_and_send_skips_when_session_not_active(br_setup, db_session):
    from app.tasks.bot_reply import _generate_and_send

    stu, course, disease = br_setup
    session = await _make_session(
        db_session, stu, course, disease, task_id="task-1", status=SessionStatus.diagnosed
    )

    with patch("app.tasks.bot_reply.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.bot_reply.gateway") as mock_gw, \
         patch("app.tasks.bot_reply.send_push") as mock_push:
        await _generate_and_send(str(session.id), "task-1")

    mock_gw.generate_patient_message.assert_not_called()
    mock_push.delay.assert_not_called()


async def test_generate_and_send_saves_reply_increments_turn_and_pushes(br_setup, db_session):
    from app.tasks.bot_reply import _generate_and_send

    stu, course, disease = br_setup
    session = await _make_session(db_session, stu, course, disease, task_id="task-1")

    with patch("app.tasks.bot_reply.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.bot_reply.gateway") as mock_gw, \
         patch("app.tasks.bot_reply.send_push") as mock_push:
        mock_gw.generate_patient_message = AsyncMock(return_value="I've been struggling to sleep.")
        await _generate_and_send(str(session.id), "task-1")

    msgs = (await db_session.execute(
        select(Message).where(Message.session_id == session.id, Message.role == MessageRole.patient)
    )).scalars().all()
    assert len(msgs) == 1
    assert msgs[0].content == "I've been struggling to sleep."
    assert msgs[0].is_nudge is False

    refreshed = (await db_session.execute(select(Session).where(Session.id == session.id))).scalar_one()
    assert refreshed.turn_count == 1
    assert refreshed.pending_reply_task_id is None

    mock_push.delay.assert_called_once()
    push_args = mock_push.delay.call_args.args
    assert push_args[0] == str(stu.id)
    assert push_args[1] == "PocketPatient"
    assert push_args[2] == "Your patient replied"
    assert push_args[3]["type"] == "new_message"
    assert push_args[3]["session_id"] == str(session.id)


async def test_generate_and_send_rolls_back_when_superseded_during_llm(br_setup, db_session):
    """A newer message claims pending_reply_task_id mid-generation: the rowcount gate
    must roll back the just-built reply rather than commit a duplicate."""
    from app.tasks.bot_reply import _generate_and_send

    stu, course, disease = br_setup
    session = await _make_session(db_session, stu, course, disease, task_id="task-1")
    session_id = session.id

    async def _llm_side_effect(*args, **kwargs):
        await db_session.execute(
            update(Session).where(Session.id == session_id).values(pending_reply_task_id="newer-task")
        )
        await db_session.commit()
        return "Reply that should never be committed."

    with patch("app.tasks.bot_reply.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.bot_reply.gateway") as mock_gw, \
         patch("app.tasks.bot_reply.send_push") as mock_push:
        mock_gw.generate_patient_message = AsyncMock(side_effect=_llm_side_effect)
        await _generate_and_send(str(session_id), "task-1")

    msgs = (await db_session.execute(
        select(Message).where(Message.session_id == session_id, Message.role == MessageRole.patient)
    )).scalars().all()
    assert len(msgs) == 0

    refreshed = (await db_session.execute(select(Session).where(Session.id == session_id))).scalar_one()
    assert refreshed.turn_count == 0
    assert refreshed.pending_reply_task_id == "newer-task"

    mock_push.delay.assert_not_called()


# --- generate_and_send_reply (sync Celery task) ---

def test_generate_and_send_reply_invokes_helper_with_session_and_task_id():
    mock_helper = MagicMock(return_value="coro-sentinel")
    with patch("app.tasks.bot_reply.run_task_async") as mock_run, \
         patch("app.tasks.bot_reply._generate_and_send", mock_helper):
        mock_run.return_value = None

        from app.tasks.bot_reply import generate_and_send_reply
        generate_and_send_reply.apply(args=["session-id-str"], task_id="my-task-id")

        mock_helper.assert_called_once_with("session-id-str", "my-task-id")
        mock_run.assert_called_once_with("coro-sentinel")


def test_generate_and_send_reply_retries_on_exception():
    # Stub the helper too, else the real _generate_and_send(...) coroutine is built
    # to pass into the mocked run_task_async and, never awaited, leaks a RuntimeWarning.
    with patch("app.tasks.bot_reply.run_task_async") as mock_run, \
         patch("app.tasks.bot_reply._generate_and_send", MagicMock(return_value="coro-sentinel")):
        mock_run.side_effect = Exception("LLM unavailable")

        from app.tasks.bot_reply import generate_and_send_reply
        from celery.exceptions import Retry

        with pytest.raises(Retry):
            generate_and_send_reply.apply(args=["session-id-str"], throw=True)


async def test_generate_and_send_regenerates_on_character_break(br_setup, db_session):
    from app.tasks.bot_reply import _generate_and_send

    stu, course, disease = br_setup
    session = await _make_session(db_session, stu, course, disease, task_id="task-1")

    replies = iter(["As an AI, I cannot do that.", "I just feel numb, doctor."])

    async def _side_effect(*args, **kwargs):
        return next(replies)

    with patch("app.tasks.bot_reply.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.bot_reply.gateway") as mock_gw, \
         patch("app.tasks.bot_reply.send_push"):
        mock_gw.generate_patient_message = AsyncMock(side_effect=_side_effect)
        await _generate_and_send(str(session.id), "task-1")

    rows = (await db_session.execute(
        select(Message).where(Message.session_id == session.id)
    )).scalars().all()
    system_rows = [m for m in rows if m.role == MessageRole.system]
    patient_replies = [m for m in rows if m.role == MessageRole.patient and not m.is_nudge]
    assert any("regenerated" in m.content for m in system_rows)
    assert patient_replies[-1].content == "I just feel numb, doctor."
