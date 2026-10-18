from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus

pytestmark = pytest.mark.usefixtures("clean_tables")


def _ctx(db_session):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.fixture(autouse=True)
def _inside_messaging_window():
    """Default every test to inside the course's messaging window so results
    don't depend on the wall-clock time the suite runs at."""
    with patch("app.tasks.nudge.is_within_messaging_window", return_value=True) as m:
        yield m


@pytest_asyncio.fixture
async def nudge_setup(professor, student, db_session):
    prof, _ = professor
    stu, _ = student
    return prof, stu


async def _make_course_with_disease(db_session, prof, stu, *, frequency):
    """Each session needs its own (user, course) pair — the partial unique
    index on active sessions allows only one active session per pair."""
    course = Course(
        title=f"Nudge Course {uuid.uuid4().hex[:6]}", professor_id=prof.id,
        class_code=uuid.uuid4().hex[:6].upper(),
    )
    db_session.add(course)
    await db_session.flush()

    unit = Unit(
        course_id=course.id, label="Unit 1",
        status=UnitStatus.released, release_date=datetime.now(timezone.utc),
    )
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id, name=f"Disease-{frequency}-{uuid.uuid4().hex[:6]}",
        category="Mood", key_symptoms=["low mood"], differentials=["GAD"],
        difficulty_tier=2, speech_style="flat",
        nudge_behavior={"frequency": frequency, "tone": "anxious", "example": "hello?"},
    )
    db_session.add(disease)
    await db_session.flush()

    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    await db_session.commit()
    await db_session.refresh(course)
    return course, disease


async def _make_session(
    db_session, stu, course, disease, *,
    last_message_hours_ago: float,
    last_message_role: MessageRole = MessageRole.patient,
    last_message_is_nudge: bool = False,
):
    now = datetime.now(timezone.utc)
    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=now - timedelta(hours=last_message_hours_ago + 1),
        status=SessionStatus.active, turn_count=1,
    )
    db_session.add(session)
    await db_session.flush()

    db_session.add(Message(
        session_id=session.id, role=last_message_role, content="...",
        sent_at=now - timedelta(hours=last_message_hours_ago), is_nudge=last_message_is_nudge,
    ))
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def _nudge_messages(db_session, session_id):
    return (
        await db_session.execute(
            select(Message).where(Message.session_id == session_id, Message.is_nudge == True)  # noqa: E712
        )
    ).scalars().all()


async def _run_check(db_session, *, gateway_text="Hey, are you there?"):
    with patch("app.tasks.nudge.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.nudge.gateway") as mock_gw, \
         patch("app.tasks.nudge.send_push") as mock_push:
        mock_gw.generate_nudge_message = AsyncMock(return_value=gateway_text)
        from app.tasks.nudge import _run_nudge_check
        await _run_nudge_check()
    return mock_gw, mock_push


async def test_skips_session_whose_last_message_is_from_student(nudge_setup, db_session):
    prof, stu = nudge_setup
    course, disease = await _make_course_with_disease(db_session, prof, stu, frequency="high")
    session = await _make_session(
        db_session, stu, course, disease,
        last_message_hours_ago=10, last_message_role=MessageRole.student,
    )

    _, mock_push = await _run_check(db_session)

    assert await _nudge_messages(db_session, session.id) == []
    mock_push.delay.assert_not_called()


async def test_skips_session_silent_for_less_than_min_cadence(nudge_setup, db_session):
    prof, stu = nudge_setup
    course, disease = await _make_course_with_disease(db_session, prof, stu, frequency="high")
    session = await _make_session(db_session, stu, course, disease, last_message_hours_ago=1)

    _, mock_push = await _run_check(db_session)

    assert await _nudge_messages(db_session, session.id) == []
    mock_push.delay.assert_not_called()


async def test_first_nudge_waits_tier_specific_silence(nudge_setup, db_session):
    """First follow-up: high after 2h, medium after 4h, low after 8h of silence."""
    prof, stu = nudge_setup
    cases = [  # (tier, hours silent, expect nudge)
        ("high", 1.5, False), ("high", 2.5, True),
        ("medium", 3.5, False), ("medium", 4.5, True),
        ("low", 7, False), ("low", 9, True),
        (" High ", 2.5, True),  # tiers come from uploaded docs: case/space-insensitive
    ]
    sessions = []
    for tier, hours, expected in cases:
        course, disease = await _make_course_with_disease(db_session, prof, stu, frequency=tier)
        sessions.append((await _make_session(
            db_session, stu, course, disease, last_message_hours_ago=hours), tier, hours, expected))

    await _run_check(db_session)

    for session, tier, hours, expected in sessions:
        got = len(await _nudge_messages(db_session, session.id))
        assert got == (1 if expected else 0), (tier, hours)


async def test_dead_tier_regression_high_fires_before_medium_before_low(nudge_setup, db_session):
    """At 6h since the last nudge: high (4h) should fire, medium (12h) and low (24h)
    should not — proving each cadence tier is independently reachable."""
    prof, stu = nudge_setup
    high_course, high_disease = await _make_course_with_disease(db_session, prof, stu, frequency="high")
    medium_course, medium_disease = await _make_course_with_disease(db_session, prof, stu, frequency="medium")
    low_course, low_disease = await _make_course_with_disease(db_session, prof, stu, frequency="low")

    high_session = await _make_session(
        db_session, stu, high_course, high_disease,
        last_message_hours_ago=6, last_message_is_nudge=True,
    )
    medium_session = await _make_session(
        db_session, stu, medium_course, medium_disease,
        last_message_hours_ago=6, last_message_is_nudge=True,
    )
    low_session = await _make_session(
        db_session, stu, low_course, low_disease,
        last_message_hours_ago=6, last_message_is_nudge=True,
    )

    await _run_check(db_session)

    assert len(await _nudge_messages(db_session, high_session.id)) == 2  # the seeded one + new
    assert len(await _nudge_messages(db_session, medium_session.id)) == 1  # only the seeded one
    assert len(await _nudge_messages(db_session, low_session.id)) == 1  # only the seeded one


async def test_unrecognized_frequency_defaults_to_medium(nudge_setup, db_session):
    prof, stu = nudge_setup
    course1, disease1 = await _make_course_with_disease(db_session, prof, stu, frequency="extreme")
    course2, disease2 = await _make_course_with_disease(db_session, prof, stu, frequency="extreme")
    too_soon = await _make_session(
        db_session, stu, course1, disease1,
        last_message_hours_ago=10, last_message_is_nudge=True,
    )
    eligible = await _make_session(
        db_session, stu, course2, disease2,
        last_message_hours_ago=30, last_message_is_nudge=True,
    )

    await _run_check(db_session)

    assert len(await _nudge_messages(db_session, too_soon.id)) == 1  # unchanged
    assert len(await _nudge_messages(db_session, eligible.id)) == 2  # seeded + new


async def test_saves_nudge_message_and_dispatches_matching_push(nudge_setup, db_session):
    prof, stu = nudge_setup
    course, disease = await _make_course_with_disease(db_session, prof, stu, frequency="high")
    session = await _make_session(db_session, stu, course, disease, last_message_hours_ago=25)

    mock_gw, mock_push = await _run_check(db_session, gateway_text="Doc, are you getting my messages?")

    nudges = await _nudge_messages(db_session, session.id)
    assert len(nudges) == 1
    assert nudges[0].role == MessageRole.patient
    assert nudges[0].content == "Doc, are you getting my messages?"

    mock_gw.generate_nudge_message.assert_called_once()

    mock_push.delay.assert_called_once()
    push_args = mock_push.delay.call_args.args
    assert push_args[0] == str(stu.id)
    assert push_args[1] == "PocketPatient"
    assert push_args[2] == "Your patient replied"
    assert push_args[3]["type"] == "new_message"
    assert push_args[3]["session_id"] == str(session.id)


async def test_nudge_leaking_disease_name_is_suppressed(nudge_setup, db_session):
    """A nudge whose raw LLM output names the diagnosis must be regenerated/
    suppressed by the character guardrail, not delivered verbatim."""
    from app.services.character_guardrail import FALLBACK_TEXT

    prof, stu = nudge_setup
    course, disease = await _make_course_with_disease(db_session, prof, stu, frequency="high")
    session = await _make_session(db_session, stu, course, disease, last_message_hours_ago=25)

    leaking = f"Doctor, I'm sure this is {disease.name}. Please respond."
    _, mock_push = await _run_check(db_session, gateway_text=leaking)

    nudges = await _nudge_messages(db_session, session.id)
    assert len(nudges) == 1
    # The disease name must not reach the student; the guardrail falls back.
    assert disease.name not in nudges[0].content
    assert nudges[0].content == FALLBACK_TEXT
    mock_push.delay.assert_called_once()


async def test_one_session_failing_does_not_abort_the_rest(nudge_setup, db_session):
    """A transient LLM failure on one eligible session must not prevent nudges
    for the others — each session is committed and pushed independently."""
    prof, stu = nudge_setup
    course1, disease1 = await _make_course_with_disease(db_session, prof, stu, frequency="high")
    course2, disease2 = await _make_course_with_disease(db_session, prof, stu, frequency="high")
    s1 = await _make_session(db_session, stu, course1, disease1, last_message_hours_ago=25)
    s2 = await _make_session(db_session, stu, course2, disease2, last_message_hours_ago=25)
    # Capture ids up front: the production rollback on the failing session expires
    # these ORM objects (the worker reuses this test's db_session), and re-reading
    # an attribute afterward would trigger a sync lazy-load on an async session.
    s1_id, s2_id = s1.id, s2.id

    with patch("app.tasks.nudge.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.nudge.gateway") as mock_gw, \
         patch("app.tasks.nudge.send_push") as mock_push:
        # First session processed raises; the second must still succeed.
        mock_gw.generate_nudge_message = AsyncMock(side_effect=[Exception("LLM down"), "recovered"])
        from app.tasks.nudge import _run_nudge_check
        await _run_nudge_check()  # must not raise

    total_nudges = len(await _nudge_messages(db_session, s1_id)) + len(await _nudge_messages(db_session, s2_id))
    assert total_nudges == 1
    mock_push.delay.assert_called_once()


def test_check_and_send_nudges_invokes_run_check():
    mock_run = MagicMock(return_value="coro-sentinel")
    with patch("app.tasks.nudge.run_task_async") as mock_rta, \
         patch("app.tasks.nudge._run_nudge_check", mock_run):
        mock_rta.return_value = None

        from app.tasks.nudge import check_and_send_nudges
        check_and_send_nudges.apply()

        mock_run.assert_called_once_with()
        mock_rta.assert_called_once_with("coro-sentinel")


async def test_no_nudge_outside_messaging_window(nudge_setup, db_session, _inside_messaging_window):
    """A student who went quiet in the evening must not get a 3 AM follow-up:
    the nudge waits for the course's window, then fires on the next hourly check."""
    prof, stu = nudge_setup
    course, disease = await _make_course_with_disease(db_session, prof, stu, frequency="medium")
    session = await _make_session(db_session, stu, course, disease, last_message_hours_ago=25)

    _inside_messaging_window.return_value = False
    mock_gw, mock_push = await _run_check(db_session)
    assert await _nudge_messages(db_session, session.id) == []
    mock_gw.generate_nudge_message.assert_not_called()
    mock_push.delay.assert_not_called()
    assert _inside_messaging_window.call_args.args[0].id == course.id

    _inside_messaging_window.return_value = True
    await _run_check(db_session)
    assert len(await _nudge_messages(db_session, session.id)) == 1
