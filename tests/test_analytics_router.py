from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus

pytestmark = pytest.mark.usefixtures("clean_tables")


@pytest_asyncio.fixture
async def seeded_course(db_session, professor, student):
    prof, _ = professor
    stu, _ = student
    course = Course(title="Psych", professor_id=prof.id, class_code="ARS123")
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(
        unit_id=unit.id, name="MDD", category="Mood", key_symptoms=["x"],
        differentials=["y"], difficulty_tier=2, speech_style="flat", nudge_behavior={},
    )
    db_session.add(disease)
    await db_session.flush()
    completed_at = datetime(2026, 8, 15, tzinfo=timezone.utc)
    sess = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=completed_at - timedelta(minutes=10), completed_at=completed_at,
        status=SessionStatus.diagnosed, turn_count=3, avg_response_latency_sec=1200,
    )
    db_session.add(sess)
    await db_session.flush()
    db_session.add(Score(session_id=sess.id, primary_dx="MDD", differentials=[],
                         justification="x" * 60, total_score=85))
    await db_session.commit()
    return course


async def test_summary_endpoint_returns_data(client, student, seeded_course):
    _, token = student
    resp = await client.get(
        f"/api/v1/analytics/student/summary?course_id={seeded_course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_cases"] == 1
    assert body["completed_cases"] == 1
    assert body["avg_score"] == 85
    assert body["scores_by_case"][0]["disease_name"] == "MDD"
    assert body["scores_by_category"]["Mood"]["count"] == 1


async def test_summary_endpoint_forbidden_for_professor(client, professor, seeded_course):
    _, token = professor
    resp = await client.get(
        f"/api/v1/analytics/student/summary?course_id={seeded_course.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_summary_endpoint_requires_course_id(client, student):
    _, token = student
    resp = await client.get(
        "/api/v1/analytics/student/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_summary_endpoint_empty(client, student):
    _, token = student
    resp = await client.get(
        f"/api/v1/analytics/student/summary?course_id={uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total_cases"] == 0


async def test_summary_endpoint_returns_cached(client, student):
    from app.main import app as fastapi_app

    _, token = student
    cid = uuid.uuid4()
    sid = str(uuid.uuid4())
    cached = {
        "total_cases": 999,
        "completed_cases": 7,
        "avg_score": 88.5,
        "avg_response_time_sec": 1234.5,
        "scores_by_case": [
            {"session_id": sid, "disease_name": "Cached MDD", "category": "Mood",
             "score": 91, "completed_at": "2026-08-15T12:00:00+00:00"}
        ],
        "scores_by_category": {"Mood": {"avg_score": 91, "count": 1}},
        "response_time_trend": [{"case_number": 1, "avg_latency_sec": 1234.5}],
        "weak_categories": [],
    }
    fastapi_app.state.redis.get = AsyncMock(return_value=json.dumps(cached))

    resp = await client.get(
        f"/api/v1/analytics/student/summary?course_id={cid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Values come straight from the cached payload, not a DB compute.
    assert body["total_cases"] == 999
    assert body["scores_by_case"][0]["session_id"] == sid
    assert body["scores_by_case"][0]["disease_name"] == "Cached MDD"
    assert body["scores_by_category"]["Mood"]["count"] == 1
