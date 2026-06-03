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

    now = datetime.now(timezone.utc)
    m1 = Message(session_id=session.id, role=MessageRole.patient, content="Hi", sent_at=now, is_nudge=False)
    m2 = Message(session_id=session.id, role=MessageRole.student, content="Hello", sent_at=now, is_nudge=False)
    db_session.add_all([m1, m2])
    await db_session.commit()

    from app.services.session_service import get_session_messages
    messages = await get_session_messages(session.id, db_session)

    assert len(messages) == 2
