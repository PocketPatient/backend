"""AI response reports: authorization, dedupe, and the admin review path."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from tests.conftest import _make_token

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _user(db_session, rsa_keys, role, verified=True):
    user = User(google_uid=f"u-{uuid.uuid4().hex}", email=f"{uuid.uuid4().hex[:8]}@test.edu",
                role=role, is_verified=verified)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, _make_token(user.id, rsa_keys[0])


@pytest_asyncio.fixture
async def setup(professor, student, db_session):
    prof, prof_token = professor
    stu, stu_token = student
    course = Course(title="Reports", professor_id=prof.id, class_code="RPT234", is_active=True)
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(unit_id=unit.id, name="MDD", category="Mood", key_symptoms=["low mood"],
                      differentials=["GAD"], difficulty_tier=2, speech_style="flat",
                      nudge_behavior=_NUDGE)
    db_session.add(disease)
    await db_session.flush()
    session = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                      started_at=datetime.now(timezone.utc), status=SessionStatus.active)
    db_session.add(session)
    await db_session.flush()
    ai_msg = Message(session_id=session.id, role=MessageRole.patient,
                     content="Some AI text", sent_at=datetime.now(timezone.utc))
    own_msg = Message(session_id=session.id, role=MessageRole.student,
                      content="My question", sent_at=datetime.now(timezone.utc))
    db_session.add_all([ai_msg, own_msg])
    await db_session.commit()
    return prof_token, stu_token, session, ai_msg, own_msg


async def _report(client, token, message_id, **extra):
    return await client.post("/api/v1/reports/ai-response",
                             json={"message_id": str(message_id), "reason": "harmful", **extra},
                             headers=_auth(token))


async def test_student_reports_ai_message_in_own_session(client, setup):
    _, stu_token, session, ai_msg, _ = setup
    resp = await _report(client, stu_token, ai_msg.id, comment="  felt unsafe  ")
    assert resp.status_code == 201
    body = resp.json()
    assert body["message_id"] == str(ai_msg.id)
    assert body["session_id"] == str(session.id)
    assert body["status"] == "open"


async def test_duplicate_report_returns_existing(client, setup):
    _, stu_token, _, ai_msg, _ = setup
    first = await _report(client, stu_token, ai_msg.id)
    second = await _report(client, stu_token, ai_msg.id)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


async def test_cannot_report_own_student_message(client, setup):
    _, stu_token, _, _, own_msg = setup
    assert (await _report(client, stu_token, own_msg.id)).status_code == 422


async def test_other_student_gets_404(client, setup, db_session, rsa_keys):
    _, _, _, ai_msg, _ = setup
    _, other_token = await _user(db_session, rsa_keys, UserRole.student)
    assert (await _report(client, other_token, ai_msg.id)).status_code == 404


async def test_unknown_message_404(client, setup):
    _, stu_token, _, _, _ = setup
    assert (await _report(client, stu_token, uuid.uuid4())).status_code == 404


async def test_course_professor_can_report_other_professor_cannot(client, setup, db_session, rsa_keys):
    prof_token, _, _, ai_msg, _ = setup
    assert (await _report(client, prof_token, ai_msg.id)).status_code == 201
    _, other_prof_token = await _user(db_session, rsa_keys, UserRole.professor)
    assert (await _report(client, other_prof_token, ai_msg.id)).status_code == 404


async def test_comment_length_capped(client, setup):
    _, stu_token, _, ai_msg, _ = setup
    assert (await _report(client, stu_token, ai_msg.id, comment="x" * 1001)).status_code == 422


async def test_admin_review_queue_and_resolve(client, setup, db_session, rsa_keys):
    _, stu_token, _, ai_msg, _ = setup
    report_id = (await _report(client, stu_token, ai_msg.id)).json()["id"]
    admin, admin_token = await _user(db_session, rsa_keys, UserRole.admin)

    queue = await client.get("/api/v1/admin/ai-reports", headers=_auth(admin_token))
    assert queue.status_code == 200
    [item] = queue.json()
    assert item["id"] == report_id
    assert item["message_content"] == "Some AI text"

    resolved = await client.post(f"/api/v1/admin/ai-reports/{report_id}/resolve",
                                 json={"resolution_note": "Tightened guardrail prompt."},
                                 headers=_auth(admin_token))
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["resolved_by"] == str(admin.id)

    assert (await client.get("/api/v1/admin/ai-reports", headers=_auth(admin_token))).json() == []


async def test_admin_endpoints_reject_non_admins(client, setup):
    prof_token, stu_token, _, _, _ = setup
    for token in (prof_token, stu_token):
        assert (await client.get("/api/v1/admin/ai-reports", headers=_auth(token))).status_code == 403
