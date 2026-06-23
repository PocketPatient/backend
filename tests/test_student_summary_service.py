from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from app.services.analytics_service import get_student_summary

pytestmark = pytest.mark.usefixtures("clean_tables")


async def _disease(db, unit_id, name, category):
    d = Disease(
        unit_id=unit_id, name=name, category=category,
        key_symptoms=["x"], differentials=["y"], difficulty_tier=2,
        speech_style="flat", nudge_behavior={},
    )
    db.add(d)
    await db.flush()
    return d


async def _completed(db, *, disease, user_id, course_id, score, latency, completed_at):
    s = Session(
        disease_id=disease.id, user_id=user_id, course_id=course_id,
        started_at=completed_at - timedelta(minutes=10), completed_at=completed_at,
        status=SessionStatus.diagnosed, turn_count=4, avg_response_latency_sec=latency,
    )
    db.add(s)
    await db.flush()
    db.add(Score(
        session_id=s.id, primary_dx=disease.name, differentials=[],
        justification="x" * 60, total_score=score,
    ))
    await db.flush()
    return s


@pytest_asyncio.fixture
async def summary_setup(db_session):
    prof = User(google_uid="su-prof", email="su-prof@test.edu", role=UserRole.professor, is_verified=True)
    stu = User(google_uid="su-stu", email="su-stu@test.edu", role=UserRole.student, is_verified=True)
    other = User(google_uid="su-other", email="su-other@test.edu", role=UserRole.student, is_verified=True)
    db_session.add_all([prof, stu, other])
    await db_session.flush()

    course = Course(title="Psych", professor_id=prof.id, class_code="SUM123")
    db_session.add(course)
    await db_session.flush()

    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()

    mdd = await _disease(db_session, unit.id, "MDD", "Mood")
    scz = await _disease(db_session, unit.id, "Schizophrenia", "Psychotic")

    base = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    # Two Mood cases (avg 80) and one Psychotic case (50 -> weak).
    await _completed(db_session, disease=mdd, user_id=stu.id, course_id=course.id,
                     score=90, latency=3600, completed_at=base)
    await _completed(db_session, disease=mdd, user_id=stu.id, course_id=course.id,
                     score=70, latency=2400, completed_at=base + timedelta(days=1))
    await _completed(db_session, disease=scz, user_id=stu.id, course_id=course.id,
                     score=50, latency=1800, completed_at=base + timedelta(days=2))
    # One active (incomplete) session -> counts toward total_cases only.
    active = Session(
        disease_id=mdd.id, user_id=stu.id, course_id=course.id,
        started_at=base + timedelta(days=3), status=SessionStatus.active, turn_count=1,
    )
    db_session.add(active)
    # Another student's completed case -> must NOT leak into stu's summary.
    await _completed(db_session, disease=mdd, user_id=other.id, course_id=course.id,
                     score=10, latency=9999, completed_at=base)
    await db_session.commit()
    return stu, course


async def test_summary_counts_and_averages(summary_setup, db_session):
    stu, course = summary_setup
    out = await get_student_summary(stu.id, course.id, db_session)
    assert out.total_cases == 4          # 3 diagnosed + 1 active
    assert out.completed_cases == 3
    assert out.avg_score == pytest.approx(70.0)   # (90+70+50)/3
    assert out.avg_response_time_sec == pytest.approx(2600.0)  # (3600+2400+1800)/3


async def test_summary_scores_by_case_ordered(summary_setup, db_session):
    stu, course = summary_setup
    out = await get_student_summary(stu.id, course.id, db_session)
    assert [c.score for c in out.scores_by_case] == [90, 70, 50]
    assert out.scores_by_case[0].disease_name == "MDD"
    assert out.scores_by_case[2].category == "Psychotic"


async def test_summary_category_and_weak(summary_setup, db_session):
    stu, course = summary_setup
    out = await get_student_summary(stu.id, course.id, db_session)
    assert out.scores_by_category["Mood"].avg_score == pytest.approx(80.0)
    assert out.scores_by_category["Mood"].count == 2
    assert out.scores_by_category["Psychotic"].avg_score == pytest.approx(50.0)
    assert out.weak_categories == ["Psychotic"]


async def test_summary_response_time_trend(summary_setup, db_session):
    stu, course = summary_setup
    out = await get_student_summary(stu.id, course.id, db_session)
    assert [p.case_number for p in out.response_time_trend] == [1, 2, 3]
    assert [p.avg_latency_sec for p in out.response_time_trend] == [3600, 2400, 1800]


async def test_summary_empty(db_session, summary_setup):
    stu, course = summary_setup
    out = await get_student_summary(uuid.uuid4(), course.id, db_session)
    assert out.total_cases == 0
    assert out.completed_cases == 0
    assert out.avg_score is None
    assert out.avg_response_time_sec is None
    assert out.scores_by_case == []
    assert out.scores_by_category == {}
    assert out.response_time_trend == []
    assert out.weak_categories == []
