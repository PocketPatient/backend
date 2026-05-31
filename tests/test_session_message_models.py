from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_session_model_create(db_session, clean_tables):
    professor = User(
        google_uid="model-test-prof",
        email="modeltest-prof@example.com",
        role=UserRole.professor,
        is_verified=True,
    )
    db_session.add(professor)
    await db_session.flush()

    student = User(
        google_uid="model-test-stu",
        email="modeltest-stu@example.com",
        role=UserRole.student,
        is_verified=True,
    )
    db_session.add(student)
    await db_session.flush()

    course = Course(
        title="Model Test Course",
        professor_id=professor.id,
        class_code="MDL234",
    )
    db_session.add(course)
    await db_session.flush()

    unit = Unit(course_id=course.id, label="Unit 1", status=UnitStatus.released)
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id,
        name="Test Disease",
        category="Mood",
        key_symptoms=["symptom1"],
        differentials=["diff1"],
        difficulty_tier=1,
        speech_style="flat",
        nudge_behavior={"frequency": "low", "tone": "neutral", "example": ""},
    )
    db_session.add(disease)
    await db_session.flush()

    session = Session(
        disease_id=disease.id,
        user_id=student.id,
        course_id=course.id,
        started_at=datetime.now(timezone.utc),
        status=SessionStatus.active,
        turn_count=0,
    )
    db_session.add(session)
    await db_session.commit()

    result = (
        await db_session.execute(select(Session).where(Session.id == session.id))
    ).scalar_one()
    assert result.status == SessionStatus.active
    assert result.turn_count == 0
    assert result.completed_at is None


@pytest.mark.asyncio
async def test_message_model_create(db_session, clean_tables):
    professor = User(
        google_uid="msg-test-prof",
        email="msgtest-prof@example.com",
        role=UserRole.professor,
        is_verified=True,
    )
    db_session.add(professor)
    await db_session.flush()

    student = User(
        google_uid="msg-test-stu",
        email="msgtest-stu@example.com",
        role=UserRole.student,
        is_verified=True,
    )
    db_session.add(student)
    await db_session.flush()

    course = Course(
        title="Msg Test Course",
        professor_id=professor.id,
        class_code="MSG234",
    )
    db_session.add(course)
    await db_session.flush()

    unit = Unit(course_id=course.id, label="Unit 1", status=UnitStatus.released)
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id,
        name="Msg Disease",
        category="Mood",
        key_symptoms=["s1"],
        differentials=["d1"],
        difficulty_tier=2,
        speech_style="flat",
        nudge_behavior={"frequency": "low", "tone": "neutral", "example": ""},
    )
    db_session.add(disease)
    await db_session.flush()

    session = Session(
        disease_id=disease.id,
        user_id=student.id,
        course_id=course.id,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    await db_session.flush()

    msg = Message(
        session_id=session.id,
        role=MessageRole.student,
        content="Hello, I have a headache.",
        sent_at=datetime.now(timezone.utc),
        is_nudge=False,
    )
    db_session.add(msg)
    await db_session.commit()

    result = (
        await db_session.execute(select(Message).where(Message.id == msg.id))
    ).scalar_one()
    assert result.role == MessageRole.student
    assert result.content == "Hello, I have a headache."
    assert result.is_nudge is False
    assert result.delivered_at is None
