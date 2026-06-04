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
    assert score == pytest.approx(75.53, abs=0.1)


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


async def test_grade_diagnosis_averages_multiple_student_latencies(graded_setup, db_session):
    db_session.add(Message(session_id=graded_setup.id, role=MessageRole.student,
                           content="And then?", sent_at=datetime.now(timezone.utc),
                           is_nudge=False, response_latency_sec=1200.0))
    await db_session.commit()

    with patch("app.services.grading_service.gateway") as gw:
        gw.grade_diagnosis = AsyncMock(return_value={
            "is_correct": True, "rubric_score": 80.0, "feedback": "ok"})
        await grade_diagnosis(graded_setup, _submission(), db_session)

    # mean of 600.0 and 1200.0
    assert graded_setup.avg_response_latency_sec == 900.0
