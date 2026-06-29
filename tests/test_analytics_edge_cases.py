from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.services.analytics_service import get_class_summary, get_student_summary

pytestmark = pytest.mark.usefixtures("clean_tables")


async def _make_course(db, professor, code: str) -> Course:
    prof, _ = professor
    course = Course(title="Psych", professor_id=prof.id, class_code=code)
    db.add(course)
    await db.flush()
    return course


async def _make_unit(db, course) -> Unit:
    unit = Unit(
        course_id=course.id, label="U1", status=UnitStatus.released,
        release_date=datetime.now(timezone.utc),
    )
    db.add(unit)
    await db.flush()
    return unit


async def _make_disease(db, unit, name="MDD", category="Mood") -> Disease:
    disease = Disease(
        unit_id=unit.id, name=name, category=category, key_symptoms=["x"],
        differentials=["y"], difficulty_tier=2, speech_style="flat", nudge_behavior={},
    )
    db.add(disease)
    await db.flush()
    return disease


async def _diagnosed(db, disease, user, course, score: float | None) -> Session:
    completed_at = datetime(2026, 8, 15, tzinfo=timezone.utc)
    sess = Session(
        disease_id=disease.id, user_id=user.id, course_id=course.id,
        started_at=completed_at - timedelta(minutes=5), completed_at=completed_at,
        status=SessionStatus.diagnosed, turn_count=3, avg_response_latency_sec=100.0,
    )
    db.add(sess)
    await db.flush()
    if score is not None:
        db.add(Score(session_id=sess.id, primary_dx="MDD", differentials=[],
                     justification="x" * 60, total_score=score))
    await db.flush()
    return sess


# --- Edge case 1: student with 0 completed cases ---

async def test_student_with_no_completed_cases_returns_empty_state(db_session, professor, student):
    stu, _ = student
    course = await _make_course(db_session, professor, "EDG001")
    unit = await _make_unit(db_session, course)
    disease = await _make_disease(db_session, unit)
    # One active (un-diagnosed) session — not counted as completed.
    db_session.add(Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active, turn_count=1,
    ))
    await db_session.commit()

    summary = await get_student_summary(stu.id, course.id, db_session)
    assert summary.completed_cases == 0
    assert summary.avg_score is None
    assert summary.avg_response_time_sec is None
    assert summary.scores_by_case == []
    assert summary.scores_by_category == {}
    assert summary.weak_categories == []


# --- Edge case 2: student enrolled in multiple courses -> per-course analytics ---

async def test_student_summary_is_scoped_per_course(db_session, professor, student):
    stu, _ = student
    course_a = await _make_course(db_session, professor, "EDGA01")
    course_b = await _make_course(db_session, professor, "EDGB01")
    unit_a = await _make_unit(db_session, course_a)
    unit_b = await _make_unit(db_session, course_b)
    disease_a = await _make_disease(db_session, unit_a, name="A", category="Mood")
    disease_b = await _make_disease(db_session, unit_b, name="B", category="Anxiety")
    await _diagnosed(db_session, disease_a, stu, course_a, score=80)
    await _diagnosed(db_session, disease_b, stu, course_b, score=40)
    await db_session.commit()

    summary_a = await get_student_summary(stu.id, course_a.id, db_session)
    assert summary_a.completed_cases == 1
    assert summary_a.avg_score == 80
    assert set(summary_a.scores_by_category) == {"Mood"}


# --- Edge case 3: professor course with 0 completed cases -> "no data" ---

async def test_class_summary_with_no_completed_cases(db_session, professor, student):
    stu, _ = student
    course = await _make_course(db_session, professor, "EDG003")
    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    await db_session.commit()

    summary = await get_class_summary(course.id, db_session)
    assert summary.enrolled_students == 1
    assert summary.total_completed_cases == 0
    assert summary.avg_class_score is None
    assert summary.category_heatmap.students == []
    assert summary.category_heatmap.categories == []
    assert summary.flagged_students == []


# --- Edge case 4: disease never assigned doesn't appear in category breakdown ---

async def test_unassigned_disease_absent_from_breakdown(db_session, professor, student):
    stu, _ = student
    course = await _make_course(db_session, professor, "EDG004")
    unit = await _make_unit(db_session, course)
    assigned = await _make_disease(db_session, unit, name="Assigned", category="Mood")
    # Never given a session -> should not surface anywhere.
    await _make_disease(db_session, unit, name="Ghost", category="Psychotic")
    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    await _diagnosed(db_session, assigned, stu, course, score=70)
    await db_session.commit()

    student_summary = await get_student_summary(stu.id, course.id, db_session)
    assert set(student_summary.scores_by_category) == {"Mood"}

    class_summary = await get_class_summary(course.id, db_session)
    assert "Psychotic" not in class_summary.category_heatmap.categories


# --- Edge case 5: score of 0 handled correctly (no divide-by-zero, counted) ---

async def test_score_of_zero_is_included_in_averages(db_session, professor, student):
    stu, _ = student
    course = await _make_course(db_session, professor, "EDG005")
    unit = await _make_unit(db_session, course)
    disease = await _make_disease(db_session, unit, category="Mood")
    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    await _diagnosed(db_session, disease, stu, course, score=0)
    await _diagnosed(db_session, disease, stu, course, score=100)
    await db_session.commit()

    summary = await get_student_summary(stu.id, course.id, db_session)
    assert summary.completed_cases == 2
    assert summary.avg_score == 50.0  # (0 + 100) / 2, zero not dropped
    assert summary.scores_by_category["Mood"].avg_score == 50.0
    assert summary.scores_by_category["Mood"].count == 2
    assert "Mood" in summary.weak_categories  # 50 < threshold of 60

    class_summary = await get_class_summary(course.id, db_session)
    assert class_summary.avg_class_score == 50.0
