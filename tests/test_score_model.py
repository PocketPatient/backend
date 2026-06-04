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
