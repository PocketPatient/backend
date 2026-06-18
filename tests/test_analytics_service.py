from __future__ import annotations

import uuid
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
    assert stats.student_msg_len_max >= stats.student_msg_len_min
    assert stats.student_msg_len_avg is not None
    assert stats.topic_coverage_score == pytest.approx(2 / 3)
    assert set(stats.topics_covered) == {"worry", "restlessness"}
    assert stats.topics_missed == ["fatigue"]


async def test_get_session_stats_missing_raises(db_session):
    from app.services.analytics_service import get_session_stats
    with pytest.raises(ValueError):
        await get_session_stats(uuid.uuid4(), db_session)
