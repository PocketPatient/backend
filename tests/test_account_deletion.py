"""Account deletion: every linked table, Firebase identity, tokens, queued work."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from firebase_admin import auth as firebase_auth
from sqlalchemy import func, select

from app.models.ai_consent import AIConsent
from app.models.ai_report import AIReportReason, AIResponseReport
from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}
_CONFIRM = {"confirm": "DELETE"}


@pytest.fixture(autouse=True)
def _no_broker():
    """Don't let celery.control.revoke reach a real broker (6s timeout per call)."""
    from app.celery_app import celery

    with patch.object(celery.control, "revoke"):
        yield


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _delete(client, token, body=_CONFIRM):
    return await client.request("DELETE", "/api/v1/users/me", json=body, headers=_auth(token))


def _ctx(db_session):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest_asyncio.fixture
async def world(professor, student, db_session):
    """A course with a student who has data in every user-linked table."""
    prof, prof_token = professor
    stu, stu_token = student
    stu.fcm_token = "device-token"
    course = Course(title="Del 101", professor_id=prof.id, class_code="DEL234", is_active=True)
    db_session.add(course)
    await db_session.flush()
    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(unit_id=unit.id, name="MDD", category="Mood", key_symptoms=["low mood"],
                      differentials=["GAD"], difficulty_tier=2, speech_style="flat",
                      nudge_behavior=_NUDGE)
    db_session.add(disease)
    await db_session.flush()
    done = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                   started_at=datetime.now(timezone.utc), status=SessionStatus.diagnosed)
    active = Session(disease_id=disease.id, user_id=stu.id, course_id=course.id,
                     started_at=datetime.now(timezone.utc), status=SessionStatus.active,
                     pending_reply_task_id="pending-reply-task")
    db_session.add_all([done, active])
    await db_session.flush()
    ai_msg = Message(session_id=done.id, role=MessageRole.patient, content="AI says",
                     sent_at=datetime.now(timezone.utc))
    db_session.add_all([
        ai_msg,
        Message(session_id=active.id, role=MessageRole.student, content="hi",
                sent_at=datetime.now(timezone.utc)),
        Score(session_id=done.id, primary_dx="MDD", is_correct=True, total_score=90.0),
    ])
    await db_session.flush()
    db_session.add(AIResponseReport(reporter_id=stu.id, session_id=done.id,
                                    message_id=ai_msg.id, reason=AIReportReason.harmful))
    await db_session.commit()
    return prof, prof_token, stu, stu_token, course


async def _count(db_session, model, *where):
    return (await db_session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def test_student_deletion_erases_every_linked_table(client, world, db_session):
    prof, _, stu, stu_token, course = world
    original_uid = stu.google_uid
    stu_id, course_id = stu.id, course.id
    from app.celery_app import celery

    with patch("app.services.account_deletion.firebase_auth.delete_user") as fb_delete, \
         patch.object(celery.control, "revoke") as revoke:
        resp = await _delete(client, stu_token)

    assert resp.status_code == 204
    fb_delete.assert_called_once_with(original_uid)
    revoke.assert_called_once_with("pending-reply-task")

    db_session.expire_all()
    # Sessions are gone, so messages/scores/reports are checked table-wide: the
    # fixture created them only for this student.
    assert await _count(db_session, Session, Session.user_id == stu_id) == 0
    assert await _count(db_session, Message) == 0
    assert await _count(db_session, Score) == 0
    assert await _count(db_session, Enrollment, Enrollment.user_id == stu_id) == 0
    assert await _count(db_session, AIConsent, AIConsent.user_id == stu_id) == 0
    assert await _count(db_session, AIResponseReport) == 0

    tomb = (await db_session.execute(select(User).where(User.id == stu_id))).scalar_one()
    assert tomb.deleted_at is not None
    assert tomb.email == f"deleted-{stu_id}@deleted.invalid"
    assert tomb.display_name is None and tomb.fcm_token is None
    assert tomb.push_enabled is False
    assert tomb.google_uid == f"deleted:{stu_id}"

    # Course content owned by the professor is untouched.
    assert await _count(db_session, Course, Course.id == course_id) == 1


async def test_deletion_revokes_tokens_and_clears_caches(client, world):
    _, _, stu, stu_token, course = world
    from app.main import app

    redis = app.state.redis
    with patch("app.services.account_deletion.firebase_auth.delete_user"):
        assert (await _delete(client, stu_token)).status_code == 204
    redis.smembers.assert_any_await(f"refresh_user:{stu.id}")
    deleted_keys = {c.args[0] for c in redis.delete.await_args_list if c.args}
    assert f"analytics:summary:{stu.id}:{course.id}" in deleted_keys
    assert f"analytics:class:{course.id}" in deleted_keys


async def test_deleted_user_cannot_use_api_and_second_delete_is_safe(client, world):
    _, _, _, stu_token, _ = world
    with patch("app.services.account_deletion.firebase_auth.delete_user"):
        assert (await _delete(client, stu_token)).status_code == 204
        assert (await client.get("/api/v1/users/me", headers=_auth(stu_token))).status_code == 401
        assert (await _delete(client, stu_token)).status_code == 401  # not a 5xx


async def test_confirmation_required(client, world):
    _, _, _, stu_token, _ = world
    assert (await _delete(client, stu_token, body={})).status_code == 422
    assert (await _delete(client, stu_token, body={"confirm": "yes"})).status_code == 422
    assert (await client.get("/api/v1/users/me", headers=_auth(stu_token))).status_code == 200


async def test_firebase_already_gone_counts_as_success(client, world):
    _, _, _, stu_token, _ = world
    with patch("app.services.account_deletion.firebase_auth.delete_user",
               side_effect=firebase_auth.UserNotFoundError("gone")):
        assert (await _delete(client, stu_token)).status_code == 204


async def test_firebase_failure_is_retryable_and_blocks_relogin(client, world, db_session):
    _, _, stu, stu_token, _ = world
    original_uid = stu.google_uid
    stu_id = stu.id
    with patch("app.services.account_deletion.firebase_auth.delete_user",
               side_effect=RuntimeError("firebase down")), \
         patch("app.tasks.account_deletion.retry_firebase_deletion") as retry:
        resp = await _delete(client, stu_token)
    assert resp.status_code == 202
    assert resp.json()["status"] == "identity_deletion_pending"
    retry.apply_async.assert_called_once()

    db_session.expire_all()
    tomb = (await db_session.execute(select(User).where(User.id == stu_id))).scalar_one()
    assert tomb.google_uid == original_uid  # kept so the retry knows whom to delete

    # The still-live Firebase identity cannot log back into the deleted account.
    decoded = {"uid": original_uid, "email": "x@rutgers.edu", "email_verified": True}
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded):
        login = await client.post("/api/v1/auth/login", json={"firebase_id_token": "t"})
    assert login.status_code == 403

    # The sweep finishes the job once Firebase recovers.
    from app.tasks.account_deletion import _sweep
    with patch("app.tasks.account_deletion.AsyncSessionLocal", return_value=_ctx(db_session)), \
         patch("app.services.account_deletion.firebase_auth.delete_user") as fb_delete:
        assert await _sweep() == 0
    fb_delete.assert_called_once_with(original_uid)
    db_session.expire_all()
    tomb = (await db_session.execute(select(User).where(User.id == stu_id))).scalar_one()
    assert tomb.google_uid == f"deleted:{stu_id}"


async def test_professor_deletion_deidentifies_and_keeps_course_and_student_work(client, world, db_session):
    prof, prof_token, stu, _, course = world
    prof_id, stu_id, course_id = prof.id, stu.id, course.id
    with patch("app.services.account_deletion.firebase_auth.delete_user"):
        assert (await _delete(client, prof_token)).status_code == 204
    db_session.expire_all()
    tomb = (await db_session.execute(select(User).where(User.id == prof_id))).scalar_one()
    assert tomb.deleted_at is not None
    assert tomb.email.endswith("@deleted.invalid")
    assert tomb.display_name is None
    kept = (await db_session.execute(select(Course).where(Course.id == course_id))).scalar_one()
    assert kept.professor_id == prof_id
    assert await _count(db_session, Session, Session.user_id == stu_id) == 2
    assert await _count(db_session, Enrollment, Enrollment.user_id == stu_id) == 1


async def test_queued_push_after_deletion_is_dropped(client, world, db_session):
    _, _, stu, stu_token, _ = world
    with patch("app.services.account_deletion.firebase_auth.delete_user"):
        assert (await _delete(client, stu_token)).status_code == 204
    from app.tasks.push_notifications import _get_push_state
    with patch("app.tasks.push_notifications.AsyncSessionLocal", return_value=_ctx(db_session)):
        state = await _get_push_state(str(stu.id))
    assert state.token is None


async def test_same_person_can_sign_up_again_after_deletion(client, world, rsa_keys, monkeypatch):
    _, _, stu, stu_token, _ = world
    from app.config import settings
    monkeypatch.setattr(settings, "jwt_private_key", rsa_keys[0])  # login mints a real JWT
    old_email = stu.email
    with patch("app.services.account_deletion.firebase_auth.delete_user"):
        assert (await _delete(client, stu_token)).status_code == 204
    decoded = {"uid": f"new-{uuid.uuid4().hex}", "email": old_email, "email_verified": True}
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded), \
         patch("app.services.auth_service.settings.allow_non_rutgers_accounts", True):
        login = await client.post("/api/v1/auth/login", json={"firebase_id_token": "t"})
    assert login.status_code == 200
