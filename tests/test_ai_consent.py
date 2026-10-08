"""AI consent: endpoints, audit trail, and the gate on EVERY Gemini call path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.models.ai_consent import AIConsent
from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "high", "tone": "neutral", "example": ""}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _ctx(db_session):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest_asyncio.fixture
async def setup(professor, student, db_session):
    prof, _ = professor
    stu, stu_token = student
    course = Course(title="Consent 101", professor_id=prof.id, class_code="CNS234", is_active=True)
    db_session.add(course)
    await db_session.flush()
    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(unit_id=unit.id, name="GAD", category="Anxiety", key_symptoms=["worry"],
                      differentials=["MDD"], difficulty_tier=2, speech_style="anxious",
                      nudge_behavior=_NUDGE)
    db_session.add(disease)
    await db_session.flush()
    session = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                      started_at=datetime.now(timezone.utc) - timedelta(days=3),
                      status=SessionStatus.active, turn_count=1, pending_reply_task_id="task-1")
    db_session.add(session)
    await db_session.flush()
    # Last message is an old patient message: nudge-eligible.
    db_session.add(Message(session_id=session.id, role=MessageRole.patient, content="Hello.",
                           sent_at=datetime.now(timezone.utc) - timedelta(days=2)))
    await db_session.commit()
    return stu, stu_token, course, disease, session


async def _revoke(client, token):
    resp = await client.delete("/api/v1/users/me/ai-consent", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["active"] is False


def _assert_consent_required(resp):
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "AI_CONSENT_REQUIRED"
    assert body["required_version"] == settings.ai_consent_version


# ── endpoints + audit trail ───────────────────────────────────────────────────

async def test_new_user_has_no_consent_then_accepts(client, professor):
    _, token = professor  # professor fixture has no consent row
    resp = await client.get("/api/v1/users/me/ai-consent", headers=_auth(token))
    assert resp.json() == {"required_version": settings.ai_consent_version, "active": False,
                           "accepted_version": None, "accepted_at": None}
    resp = await client.put("/api/v1/users/me/ai-consent", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["accepted_version"] == settings.ai_consent_version
    assert body["accepted_at"] is not None


async def test_accept_is_idempotent(client, professor):
    _, token = professor
    first = (await client.put("/api/v1/users/me/ai-consent", headers=_auth(token))).json()
    second = (await client.put("/api/v1/users/me/ai-consent", headers=_auth(token))).json()
    assert first["accepted_at"] == second["accepted_at"]


async def test_revoke_and_reaccept_keeps_audit_history(client, student, db_session):
    stu, token = student
    await _revoke(client, token)
    await client.put("/api/v1/users/me/ai-consent", headers=_auth(token))
    rows = (await db_session.execute(
        select(AIConsent).where(AIConsent.user_id == stu.id).order_by(AIConsent.accepted_at)
    )).scalars().all()
    assert len(rows) == 2
    assert rows[0].revoked_at is not None
    assert rows[1].revoked_at is None
    assert all(r.version == settings.ai_consent_version for r in rows)


async def test_new_disclosure_version_supersedes_old_consent(client, setup, db_session, monkeypatch):
    _, token, course, _, session = setup
    session.status = SessionStatus.abandoned  # free the one-active-session slot
    await db_session.commit()
    monkeypatch.setattr(settings, "ai_consent_version", "2099-01-v9")
    status = (await client.get("/api/v1/users/me/ai-consent", headers=_auth(token))).json()
    assert status["active"] is False
    with patch("app.services.session_service.gateway") as gw:
        gw.generate_opening_message = AsyncMock(return_value="hi")
        resp = await client.post("/api/v1/sessions", json={"course_id": str(course.id)},
                                 headers=_auth(token))
    _assert_consent_required(resp)
    gw.generate_opening_message.assert_not_called()


# ── the gate: every Gemini entry point ────────────────────────────────────────

async def test_gate_create_session(client, setup, db_session):
    _, token, course, _, session = setup
    session.status = SessionStatus.abandoned  # free the one-active-session slot
    await db_session.commit()
    await _revoke(client, token)
    with patch("app.services.session_service.gateway") as gw:
        gw.generate_opening_message = AsyncMock(return_value="hi")
        resp = await client.post("/api/v1/sessions", json={"course_id": str(course.id)},
                                 headers=_auth(token))
    _assert_consent_required(resp)
    gw.generate_opening_message.assert_not_called()


async def test_gate_send_message_persists_nothing_and_queues_nothing(client, setup, db_session):
    _, token, _, _, session = setup
    await _revoke(client, token)
    with patch("app.services.session_service.celery"), \
         patch("app.tasks.bot_reply.generate_and_send_reply") as task:
        resp = await client.post(f"/api/v1/sessions/{session.id}/messages",
                                 json={"content": "How are you?"}, headers=_auth(token))
    _assert_consent_required(resp)
    task.apply_async.assert_not_called()
    msgs = (await db_session.execute(
        select(Message).where(Message.session_id == session.id, Message.role == MessageRole.student)
    )).scalars().all()
    assert msgs == []


async def test_gate_diagnose_grading_and_hint(client, setup):
    _, token, _, _, session = setup
    await _revoke(client, token)
    with patch("app.services.grading_service.gateway") as gw:
        gw.grade_diagnosis = AsyncMock()
        gw.generate_hint = AsyncMock()
        resp = await client.post(
            f"/api/v1/sessions/{session.id}/diagnose",
            json={"primary_dx": "GAD", "differentials": ["MDD"],
                  "justification": "Persistent excessive worry and restlessness for months."},
            headers=_auth(token),
        )
    _assert_consent_required(resp)
    gw.grade_diagnosis.assert_not_called()
    gw.generate_hint.assert_not_called()


async def test_gate_bot_reply_task_rechecks_at_execution(client, setup, db_session):
    """Consent revoked AFTER the reply was queued: the task must not call Gemini."""
    from app.tasks.bot_reply import _generate_and_send

    _, token, _, _, session = setup
    await _revoke(client, token)
    with patch("app.tasks.bot_reply.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.bot_reply.gateway") as gw, \
         patch("app.tasks.bot_reply.send_push") as push:
        gw.generate_patient_message = AsyncMock(return_value="reply")
        await _generate_and_send(str(session.id), "task-1")
    gw.generate_patient_message.assert_not_called()
    push.delay.assert_not_called()


async def test_gate_nudge_task(client, setup, db_session):
    from app.tasks.nudge import _maybe_send_nudge

    _, token, _, _, session = setup
    await _revoke(client, token)
    with patch("app.tasks.nudge.gateway") as gw, patch("app.tasks.nudge.send_push"):
        gw.generate_nudge_message = AsyncMock(return_value="you there?")
        await _maybe_send_nudge(session.id, db_session)
    gw.generate_nudge_message.assert_not_called()


async def test_gate_auto_case_initiation_skips(client, setup, db_session):
    from app.tasks.case_initiation import _check_and_create

    stu, token, course, _, session = setup
    session.status = SessionStatus.abandoned
    await db_session.commit()
    await _revoke(client, token)
    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.services.session_service.gateway") as gw:
        gw.generate_opening_message = AsyncMock(return_value="hi")
        result = await _check_and_create(str(stu.id), str(course.id))
    assert result is None
    gw.generate_opening_message.assert_not_called()


# ── payload minimization ──────────────────────────────────────────────────────

async def test_gemini_payloads_exclude_user_identifiers(setup, db_session):
    """Bot-reply, nudge and opener prompts carry case/transcript content only."""
    from app.tasks.bot_reply import _generate_and_send
    from app.tasks.nudge import _maybe_send_nudge

    stu, _, course, _, session = setup
    stu.fcm_token = "fcm-secret-device-token"
    await db_session.commit()
    identifiers = [stu.email, stu.google_uid, str(stu.id), stu.display_name, stu.fcm_token]

    captured: list = []

    def _capture(name):
        async def _fn(*args, **kwargs):
            captured.append((name, repr(args), repr(kwargs)))
            return "Patient text."
        return _fn

    with patch("app.tasks.bot_reply.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.tasks.bot_reply.gateway") as gw_reply, \
         patch("app.tasks.bot_reply.send_push"), \
         patch("app.tasks.nudge.gateway") as gw_nudge, \
         patch("app.tasks.nudge.send_push"):
        gw_reply.generate_patient_message = _capture("reply")
        gw_nudge.generate_nudge_message = _capture("nudge")
        await _maybe_send_nudge(session.id, db_session)
        await _generate_and_send(str(session.id), "task-1")

    session.status = SessionStatus.abandoned
    await db_session.commit()
    from app.services.session_service import create_new_session
    with patch("app.services.session_service.gateway") as gw_open:
        gw_open.generate_opening_message = _capture("opener")
        await create_new_session(stu.id, course.id, db_session)

    assert {c[0] for c in captured} == {"reply", "nudge", "opener"}
    for name, args, kwargs in captured:
        for ident in identifiers:
            assert ident not in args and ident not in kwargs, f"{name} payload leaks {ident!r}"
