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

pytestmark = pytest.mark.usefixtures("clean_tables")


async def _make_completed(db, *, disease_id, user_id, course_id, score, completed_at):
    s = Session(
        disease_id=disease_id, user_id=user_id, course_id=course_id,
        started_at=completed_at - timedelta(minutes=10), completed_at=completed_at,
        status=SessionStatus.diagnosed, turn_count=3, avg_response_latency_sec=600,
    )
    db.add(s)
    await db.flush()
    db.add(Score(session_id=s.id, primary_dx="MDD", differentials=[],
                 justification="x" * 60, total_score=score))
    await db.flush()
    return s


@pytest_asyncio.fixture
async def list_setup(db_session, professor, student):
    prof, _ = professor
    stu, _ = student
    other = User(google_uid="ls-other", email="ls-other@test.edu",
                 role=UserRole.student, is_verified=True)
    db_session.add(other)
    await db_session.flush()

    course = Course(title="Psych", professor_id=prof.id, class_code="LST123")
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

    base = datetime(2026, 8, 10, tzinfo=timezone.utc)
    for i in range(3):
        await _make_completed(db_session, disease_id=disease.id, user_id=stu.id,
                              course_id=course.id, score=80 + i,
                              completed_at=base + timedelta(days=i))
    # An active session (not diagnosed) for the student.
    db_session.add(Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                           started_at=base + timedelta(days=9), status=SessionStatus.active,
                           turn_count=1))
    # Another student's completed session in the same course.
    await _make_completed(db_session, disease_id=disease.id, user_id=other.id,
                          course_id=course.id, score=10, completed_at=base)
    await db_session.commit()
    return course, stu, other


async def test_student_sees_only_own_diagnosed(client, student, list_setup):
    course, _, _ = list_setup
    _, token = student
    resp = await client.get(
        f"/api/v1/sessions?course_id={course.id}&status=diagnosed",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    # Ordered by completed_at desc -> highest score (latest) first.
    assert body["items"][0]["score"] == 82
    assert all(it["disease_name"] == "MDD" for it in body["items"])


async def test_pagination(client, student, list_setup):
    course, _, _ = list_setup
    _, token = student
    resp = await client.get(
        f"/api/v1/sessions?course_id={course.id}&status=diagnosed&page=2&page_size=2",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert body["page_size"] == 2
    assert len(body["items"]) == 1


async def test_professor_must_own_course(client, professor, student, db_session, list_setup):
    # professor fixture owns the course; a different course id they don't own -> 404
    course, _, _ = list_setup
    _, token = professor
    resp = await client.get(
        f"/api/v1/sessions?course_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_professor_filters_by_student(client, professor, list_setup):
    course, stu, other = list_setup
    _, token = professor
    resp = await client.get(
        f"/api/v1/sessions?course_id={course.id}&status=diagnosed&student_id={other.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["score"] == 10
