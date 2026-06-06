from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}


@pytest_asyncio.fixture
async def setup(db_session):
    prof = User(
        google_uid="svc-prof", email="svc-prof@test.edu",
        role=UserRole.professor, is_verified=True,
    )
    stu = User(
        google_uid="svc-stu", email="svc-stu@test.edu",
        role=UserRole.student, is_verified=True,
    )
    db_session.add_all([prof, stu])
    await db_session.flush()

    course = Course(title="Psych 101", professor_id=prof.id, class_code="SVC123")
    db_session.add(course)
    await db_session.flush()

    enrollment = Enrollment(user_id=stu.id, course_id=course.id)
    db_session.add(enrollment)

    unit = Unit(
        course_id=course.id, label="Unit 1",
        status=UnitStatus.released, release_date=datetime.now(timezone.utc),
    )
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id, name="GAD", category="Anxiety",
        key_symptoms=["worry", "restlessness"], differentials=["MDD"],
        difficulty_tier=2, speech_style="anxious", nudge_behavior=_NUDGE,
    )
    db_session.add(disease)
    await db_session.commit()
    await db_session.refresh(disease)
    await db_session.refresh(course)
    await db_session.refresh(stu)

    return prof, stu, course, disease


async def test_create_new_session_returns_session_and_opening_message(db_session, setup):
    _, stu, course, disease = setup

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_opening_message = AsyncMock(return_value="Hi, I need some help.")
        from app.services.session_service import create_new_session
        session, message = await create_new_session(stu.id, course.id, db_session)

    assert session.user_id == stu.id
    assert session.course_id == course.id
    assert session.status == SessionStatus.active
    assert session.turn_count == 0
    assert message.role == MessageRole.patient
    assert message.content == "Hi, I need some help."
    assert message.session_id == session.id


async def test_create_new_session_picks_from_disease_pool(db_session, setup):
    _, stu, course, disease = setup

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_opening_message = AsyncMock(return_value="Hello doctor.")
        from app.services.session_service import create_new_session
        session, _ = await create_new_session(stu.id, course.id, db_session)

    assert session.disease_id == disease.id


async def test_create_new_session_raises_if_no_disease_pool(db_session, setup):
    prof, stu, _, _ = setup

    empty_course = Course(title="Empty", professor_id=prof.id, class_code="EMP123")
    db_session.add(empty_course)
    await db_session.flush()
    db_session.add(Enrollment(user_id=stu.id, course_id=empty_course.id))
    await db_session.commit()

    from fastapi import HTTPException
    from app.services.session_service import create_new_session
    with pytest.raises(HTTPException) as exc_info:
        await create_new_session(stu.id, empty_course.id, db_session)
    assert exc_info.value.status_code == 422


async def test_only_one_active_session_per_user_course(db_session, setup):
    from sqlalchemy.exc import IntegrityError

    _, stu, course, disease = setup
    now = datetime.now(timezone.utc)

    s1 = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=now, status=SessionStatus.active,
    )
    db_session.add(s1)
    await db_session.commit()

    s2 = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=now, status=SessionStatus.active,
    )
    db_session.add(s2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_non_active_sessions_do_not_block_a_new_active_one(db_session, setup):
    _, stu, course, disease = setup
    now = datetime.now(timezone.utc)

    done = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=now, status=SessionStatus.diagnosed,
    )
    db_session.add(done)
    await db_session.commit()

    fresh = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=now, status=SessionStatus.active,
    )
    db_session.add(fresh)
    await db_session.commit()  # must not raise — partial index only covers active
    assert fresh.id is not None


async def test_get_active_session_returns_active(db_session, setup):
    _, stu, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()
    await db_session.refresh(session)

    from app.services.session_service import get_active_session
    result = await get_active_session(stu.id, course.id, db_session)

    assert result is not None
    assert result.id == session.id


async def test_get_active_session_returns_none_when_none(db_session, setup):
    _, stu, course, _ = setup

    from app.services.session_service import get_active_session
    result = await get_active_session(stu.id, course.id, db_session)

    assert result is None


async def test_get_session_messages_returns_ordered(db_session, setup):
    _, stu, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    await db_session.flush()

    from datetime import timedelta
    t1 = datetime.now(timezone.utc)
    t2 = t1 + timedelta(seconds=5)
    m1 = Message(session_id=session.id, role=MessageRole.patient, content="Hi", sent_at=t1, is_nudge=False)
    m2 = Message(session_id=session.id, role=MessageRole.student, content="Hello", sent_at=t2, is_nudge=False)
    db_session.add_all([m1, m2])
    await db_session.commit()

    from app.services.session_service import get_session_messages
    messages = await get_session_messages(session.id, db_session)

    assert len(messages) == 2
    assert messages[0].content == "Hi"
    assert messages[1].content == "Hello"


# --- handle_student_message ---

async def _session_with_opening(db_session, stu, course, disease, *, task_id=None):
    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active, turn_count=0,
        pending_reply_task_id=task_id,
    )
    db_session.add(session)
    await db_session.flush()
    db_session.add(Message(
        session_id=session.id, role=MessageRole.patient,
        content="Hi doc.", sent_at=datetime.now(timezone.utc), is_nudge=False,
    ))
    await db_session.commit()
    await db_session.refresh(session)
    return session


async def test_handle_student_message_saves_with_latency_and_recomputes_avg(db_session, setup):
    _, stu, course, disease = setup
    session = await _session_with_opening(db_session, stu, course, disease)

    with patch("app.services.session_service.celery"), \
         patch("app.tasks.bot_reply.generate_and_send_reply") as mock_task:
        from app.services.session_service import handle_student_message
        student_msg = await handle_student_message(
            session, "Tell me more about your symptoms.", instant=False, db=db_session
        )

    assert student_msg.role == MessageRole.student
    assert student_msg.content == "Tell me more about your symptoms."
    assert student_msg.response_latency_sec is not None
    assert student_msg.response_latency_sec >= 0

    await db_session.refresh(session)
    assert session.avg_response_latency_sec == student_msg.response_latency_sec
    mock_task.apply_async.assert_called_once()


async def test_handle_student_message_revokes_old_task_and_schedules_with_eta(db_session, setup):
    _, stu, course, disease = setup
    session = await _session_with_opening(db_session, stu, course, disease, task_id="old-task")

    with patch("app.services.session_service.celery") as mock_celery, \
         patch("app.tasks.bot_reply.generate_and_send_reply") as mock_task, \
         patch("app.services.session_service._reply_delay_seconds", return_value=120.0):
        from app.services.session_service import handle_student_message
        student_msg = await handle_student_message(
            session, "I've been having trouble sleeping.", instant=False, db=db_session
        )

    mock_celery.control.revoke.assert_called_once_with("old-task")

    await db_session.refresh(session)
    assert session.pending_reply_task_id is not None
    assert session.pending_reply_task_id != "old-task"

    mock_task.apply_async.assert_called_once()
    call_kwargs = mock_task.apply_async.call_args.kwargs
    assert call_kwargs["args"] == [str(session.id)]
    assert call_kwargs["task_id"] == session.pending_reply_task_id
    assert "eta" in call_kwargs and call_kwargs["eta"] is not None
    assert student_msg.session_id == session.id


async def test_handle_student_message_instant_dispatches_without_eta(db_session, setup):
    _, stu, course, disease = setup
    session = await _session_with_opening(db_session, stu, course, disease)

    with patch("app.services.session_service.celery"), \
         patch("app.tasks.bot_reply.generate_and_send_reply") as mock_task:
        from app.services.session_service import handle_student_message
        await handle_student_message(
            session, "Are you there?", instant=True, db=db_session
        )

    mock_task.apply_async.assert_called_once()
    call_kwargs = mock_task.apply_async.call_args.kwargs
    assert "eta" not in call_kwargs


# --- _reply_delay_seconds ---

@pytest.mark.parametrize("speech_style, expected_range", [
    ("flat", (3600, 4 * 3600)),
    ("tangential", (15 * 60, 60 * 60)),
    ("disorganized", (0, 30 * 60)),
    ("anxious", (5 * 60, 30 * 60)),  # unrecognized style falls back to the default range
])
def test_reply_delay_seconds_uses_range_for_speech_style(speech_style, expected_range):
    from app.services.session_service import _reply_delay_seconds

    with patch("app.services.session_service.random.uniform", return_value=99.0) as mock_uniform:
        result = _reply_delay_seconds(speech_style)

    mock_uniform.assert_called_once_with(*expected_range)
    assert result == 99.0


def test_reply_delay_seconds_pressured_is_immediate():
    from app.services.session_service import _reply_delay_seconds

    assert _reply_delay_seconds("pressured") == 0


# --- avg_student_latency (relocated from grading_service) ---

def _msg(role, latency):
    return Message(
        session_id=uuid.uuid4(), role=role, content="x",
        sent_at=datetime.now(timezone.utc), is_nudge=False,
        response_latency_sec=latency,
    )


def test_avg_student_latency_returns_mean_of_student_latencies():
    from app.services.session_service import avg_student_latency

    messages = [
        _msg(MessageRole.patient, None),
        _msg(MessageRole.student, 100.0),
        _msg(MessageRole.student, 300.0),
    ]
    assert avg_student_latency(messages) == 200.0


def test_avg_student_latency_returns_none_when_no_student_latencies():
    from app.services.session_service import avg_student_latency

    messages = [_msg(MessageRole.patient, None), _msg(MessageRole.student, None)]
    assert avg_student_latency(messages) is None


# --- _avg_student_latency_sql (SQL-aggregate hot-path variant) ---

async def test_avg_student_latency_sql_computes_mean_from_db(db_session, setup):
    _, stu, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.flush()
    db_session.add_all([
        Message(session_id=session.id, role=MessageRole.patient, content="hi",
                sent_at=datetime.now(timezone.utc), is_nudge=False, response_latency_sec=None),
        Message(session_id=session.id, role=MessageRole.student, content="hey",
                sent_at=datetime.now(timezone.utc), is_nudge=False, response_latency_sec=120.0),
        Message(session_id=session.id, role=MessageRole.student, content="hey2",
                sent_at=datetime.now(timezone.utc), is_nudge=False, response_latency_sec=480.0),
    ])
    await db_session.commit()

    from app.services.session_service import _avg_student_latency_sql
    result = await _avg_student_latency_sql(session.id, db_session)
    assert result == 300.0


async def test_avg_student_latency_sql_returns_none_when_no_student_latencies(db_session, setup):
    _, stu, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.flush()
    db_session.add(Message(
        session_id=session.id, role=MessageRole.patient, content="hi",
        sent_at=datetime.now(timezone.utc), is_nudge=False, response_latency_sec=None,
    ))
    await db_session.commit()

    from app.services.session_service import _avg_student_latency_sql
    result = await _avg_student_latency_sql(session.id, db_session)
    assert result is None
