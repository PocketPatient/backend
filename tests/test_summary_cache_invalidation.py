# tests/test_summary_cache_invalidation.py
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.services.analytics_cache import summary_key

pytestmark = pytest.mark.usefixtures("clean_tables")


@pytest_asyncio.fixture
async def diagnose_setup(db_session, professor, student, monkeypatch):
    prof, _ = professor
    stu, _ = student
    course = Course(title="Psych", professor_id=prof.id, class_code="INV123")
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(unit_id=unit.id, name="MDD", category="Mood", key_symptoms=["x"],
                      differentials=["y"], difficulty_tier=2, speech_style="flat",
                      nudge_behavior={})
    db_session.add(disease)
    await db_session.flush()
    sess = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                   started_at=datetime.now(timezone.utc), status=SessionStatus.active,
                   turn_count=2)
    db_session.add(sess)
    await db_session.commit()

    # Make grading deterministic + correct so a Score is committed.
    async def fake_grade(session, submission, db):
        from app.models.score import Score
        session.avg_response_latency_sec = 100.0
        return Score(session_id=session.id, primary_dx=submission.primary_dx,
                     differentials=[], justification=submission.justification,
                     is_correct=True, rubric_score=90.0, response_time_score=100.0,
                     total_score=92.0, feedback_text="ok",
                     graded_at=datetime.now(timezone.utc))

    monkeypatch.setattr("app.routers.sessions.grade_diagnosis", fake_grade)
    return course, stu, sess


async def test_diagnose_invalidates_summary_cache(client, student, diagnose_setup):
    from app.main import app as fastapi_app

    course, stu, sess = diagnose_setup
    _, token = student
    redis = AsyncMock()
    fastapi_app.state.redis = redis  # same global app the test client wraps

    resp = await client.post(
        f"/api/v1/sessions/{sess.id}/diagnose",
        headers={"Authorization": f"Bearer {token}"},
        json={"primary_dx": "MDD", "differentials": [], "justification": "x" * 60},
    )
    assert resp.status_code == 200
    assert resp.json()["correct"] is True
    redis.delete.assert_awaited_with(summary_key(stu.id, course.id))
