from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}


@pytest_asyncio.fixture
async def setup(professor, student, db_session):
    prof, prof_token = professor
    stu, stu_token = student

    course = Course(title="Psych 101", professor_id=prof.id, class_code="SES123", is_active=True)
    db_session.add(course)
    await db_session.flush()

    enrollment = Enrollment(user_id=stu.id, course_id=course.id)
    db_session.add(enrollment)

    unit = Unit(
        course_id=course.id, label="Unit 1",
        status=UnitStatus.released, release_date=datetime.now(timezone.utc),
    )
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id, name="GAD", category="Anxiety",
        key_symptoms=["worry"], differentials=["MDD"],
        difficulty_tier=2, speech_style="anxious", nudge_behavior=_NUDGE,
    )
    db_session.add(disease)
    await db_session.commit()
    await db_session.refresh(disease)
    await db_session.refresh(course)

    return prof, prof_token, stu, stu_token, course, disease


async def test_create_session_student_enrolled(client, setup):
    _, _, _, stu_token, course, _ = setup

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_opening_message = AsyncMock(return_value="Hi, I need help.")
        resp = await client.post(
            "/api/v1/sessions",
            json={"course_id": str(course.id)},
            headers={"Authorization": f"Bearer {stu_token}"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "active"
    assert data["turn_count"] == 0
    assert len(data["messages"]) == 1
    assert data["messages"][0]["role"] == "patient"
    assert data["messages"][0]["content"] == "Hi, I need help."


async def test_create_session_professor_forbidden(client, setup):
    _, prof_token, _, _, course, _ = setup

    resp = await client.post(
        "/api/v1/sessions",
        json={"course_id": str(course.id)},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 403


async def test_create_session_duplicate_returns_409(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup

    existing = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(existing)
    await db_session.commit()

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_opening_message = AsyncMock(return_value="Hello.")
        resp = await client.post(
            "/api/v1/sessions",
            json={"course_id": str(course.id)},
            headers={"Authorization": f"Bearer {stu_token}"},
        )

    assert resp.status_code == 409


async def test_create_session_not_enrolled_returns_404(client, setup, db_session, rsa_keys):
    _, _, _, _, course, _ = setup
    private_pem, _ = rsa_keys

    other_stu = User(
        google_uid=f"x-{uuid.uuid4().hex}",
        email=f"x-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.student, is_verified=True,
        display_name="Other",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(other_stu)
    await db_session.commit()

    from jose import jwt
    other_token = jwt.encode({"sub": str(other_stu.id)}, private_pem, algorithm="RS256")

    resp = await client.post(
        "/api/v1/sessions",
        json={"course_id": str(course.id)},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


async def test_get_active_session_returns_session(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.flush()

    msg = Message(
        session_id=session.id, role=MessageRole.patient,
        content="Hi doc.", sent_at=datetime.now(timezone.utc), is_nudge=False,
    )
    db_session.add(msg)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/sessions/active?course_id={course.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "Hi doc."


async def test_get_active_session_none_returns_404(client, setup):
    _, _, _, stu_token, course, _ = setup

    resp = await client.get(
        f"/api/v1/sessions/active?course_id={course.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 404


async def test_get_session_by_id_student_owner(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.flush()

    msg = Message(
        session_id=session.id, role=MessageRole.patient,
        content="Hello.", sent_at=datetime.now(timezone.utc), is_nudge=False,
    )
    db_session.add(msg)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert str(data["id"]) == str(session.id)
    assert len(data["messages"]) == 1


async def test_get_session_by_id_professor_of_course(client, setup, db_session):
    prof, prof_token, stu, _, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200


async def test_get_session_by_id_unauthorized_user_returns_404(client, setup, db_session, rsa_keys):
    _, _, stu, _, course, disease = setup
    private_pem, _ = rsa_keys

    other_stu = User(
        google_uid=f"oth-{uuid.uuid4().hex}",
        email=f"oth-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.student, is_verified=True,
        display_name="Other",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(other_stu)

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    from jose import jwt
    other_token = jwt.encode({"sub": str(other_stu.id)}, private_pem, algorithm="RS256")
    resp = await client.get(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


async def test_send_message_returns_patient_reply(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.flush()

    opening = Message(
        session_id=session.id, role=MessageRole.patient,
        content="Hi doc.", sent_at=datetime.now(timezone.utc), is_nudge=False,
    )
    db_session.add(opening)
    await db_session.commit()

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_patient_message = AsyncMock(return_value="My mood has been really low.")
        resp = await client.post(
            f"/api/v1/sessions/{session.id}/messages",
            json={"content": "Tell me how you've been feeling."},
            headers={"Authorization": f"Bearer {stu_token}"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["role"] == "patient"
    assert data["content"] == "My mood has been really low."


async def test_send_message_increments_turn_count(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active, turn_count=0,
    )
    db_session.add(session)
    await db_session.flush()

    opening = Message(
        session_id=session.id, role=MessageRole.patient,
        content="Hi.", sent_at=datetime.now(timezone.utc), is_nudge=False,
    )
    db_session.add(opening)
    await db_session.commit()

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_patient_message = AsyncMock(return_value="Feeling low.")
        await client.post(
            f"/api/v1/sessions/{session.id}/messages",
            json={"content": "How are you?"},
            headers={"Authorization": f"Bearer {stu_token}"},
        )

    resp = await client.get(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.json()["turn_count"] == 1


async def test_send_message_empty_content_returns_422(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/sessions/{session.id}/messages",
        json={"content": ""},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 422


async def test_send_message_persists_student_msg_when_llm_fails(client, setup, db_session):
    from fastapi import HTTPException

    _, _, stu, stu_token, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active, turn_count=0,
    )
    db_session.add(session)
    await db_session.flush()
    opening = Message(
        session_id=session.id, role=MessageRole.patient,
        content="Hi doc.", sent_at=datetime.now(timezone.utc), is_nudge=False,
    )
    db_session.add(opening)
    await db_session.commit()

    with patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_patient_message = AsyncMock(
            side_effect=HTTPException(status_code=502, detail="LLM down")
        )
        resp = await client.post(
            f"/api/v1/sessions/{session.id}/messages",
            json={"content": "I have been very anxious lately."},
            headers={"Authorization": f"Bearer {stu_token}"},
        )
    assert resp.status_code == 502

    # The student's message must survive the failed LLM call.
    resp2 = await client.get(
        f"/api/v1/sessions/{session.id}",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    data = resp2.json()
    contents = [m["content"] for m in data["messages"]]
    assert "I have been very anxious lately." in contents
    # No patient reply was added and the turn was not counted.
    assert [m["role"] for m in data["messages"]].count("patient") == 1
    assert data["turn_count"] == 0


async def test_send_message_session_not_active_returns_409(client, setup, db_session):
    _, _, stu, stu_token, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.diagnosed,
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/sessions/{session.id}/messages",
        json={"content": "Hello"},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 409


async def test_send_message_professor_forbidden(client, setup, db_session):
    prof, prof_token, stu, _, course, disease = setup

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.post(
        f"/api/v1/sessions/{session.id}/messages",
        json={"content": "Hello"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 403


async def test_send_message_not_owner_returns_404(client, setup, db_session, rsa_keys):
    _, _, stu, _, course, disease = setup
    private_pem, _ = rsa_keys

    other_stu = User(
        google_uid=f"msg-{uuid.uuid4().hex}",
        email=f"msg-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.student, is_verified=True,
        display_name="Other",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(other_stu)

    session = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    from jose import jwt
    other_token = jwt.encode({"sub": str(other_stu.id)}, private_pem, algorithm="RS256")
    resp = await client.post(
        f"/api/v1/sessions/{session.id}/messages",
        json={"content": "Hello"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404
