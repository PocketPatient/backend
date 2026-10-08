"""Account deletion (App Store guideline 5.1.1(v)).

Deletion map — what happens to each piece of user-linked data:

| Data                               | Behavior                                              |
|------------------------------------|-------------------------------------------------------|
| sessions, messages, scores         | DELETED (the user's own sessions; scores/messages     |
|                                    | explicitly, not via cascade)                          |
| ai_response_reports (as reporter,  | DELETED                                               |
|   or on the user's sessions)       |                                                       |
| ai_response_reports.resolved_by    | set NULL (admin who resolved others' reports)         |
| enrollments                        | DELETED                                               |
| ai_consents                        | DELETED                                               |
| users row                          | kept as a de-identified TOMBSTONE: email replaced by  |
|                                    | a unique placeholder; name, FCM token, quiet hours    |
|                                    | cleared; push disabled; deleted_at set. Kept so a     |
|                                    | professor's courses (courses.professor_id,            |
|                                    | disease_documents.uploaded_by) stay intact for the    |
|                                    | enrolled students — a professor's identity is         |
|                                    | removed, their course content is retained.            |
| courses / units / diseases /       | RETAINED (course content, not personal data)          |
|   disease documents (professor)    |                                                       |
| Firebase Auth identity             | DELETED by UID after the DB commit; on failure the    |
|                                    | tombstone keeps the UID and a Celery task retries     |
|                                    | (plus an hourly sweep) until it succeeds.             |
| Redis refresh tokens               | REVOKED (whole family)                                |
| Redis analytics cache              | invalidated for the user's courses                    |
| queued Celery work                 | pending bot replies revoked; any task that still runs |
|                                    | finds no session / no FCM token and no-ops            |

Not erasable on demand: Cloud SQL automated backups and Cloud Logging retain
data until their configured retention expires (logs contain no message bodies).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool
from firebase_admin import auth as firebase_auth
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_consent import AIConsent
from app.models.ai_report import AIResponseReport
from app.models.enrollment import Enrollment
from app.models.message import Message
from app.models.score import Score
from app.models.session import Session
from app.models.user import User
from app.services import auth_service
from app.services.analytics_cache import class_summary_key, invalidate, summary_key

logger = logging.getLogger(__name__)

# Firebase UID prefix written once the Firebase identity is confirmed deleted.
DELETED_UID_PREFIX = "deleted:"


def _placeholder_email(user_id: uuid.UUID) -> str:
    # users.email is NOT NULL + UNIQUE; .invalid is a reserved TLD (RFC 2606).
    return f"deleted-{user_id}@deleted.invalid"


async def delete_account(user_id: uuid.UUID, db: AsyncSession, redis) -> bool:
    """Erase/de-identify a user. Returns True once the Firebase identity is gone,
    False if Firebase deletion was queued for retry. Idempotent."""
    user = (
        await db.execute(select(User).where(User.id == user_id).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        return True
    if user.deleted_at is not None:
        return user.google_uid.startswith(DELETED_UID_PREFIX)

    sessions = (
        await db.execute(
            select(Session.id, Session.course_id, Session.pending_reply_task_id).where(
                Session.user_id == user_id
            )
        )
    ).all()
    session_ids = [s.id for s in sessions]
    pending_task_ids = [s.pending_reply_task_id for s in sessions if s.pending_reply_task_id]
    enrolled_course_ids = (
        await db.execute(select(Enrollment.course_id).where(Enrollment.user_id == user_id))
    ).scalars().all()
    course_ids = {s.course_id for s in sessions} | set(enrolled_course_ids)

    report_filter = AIResponseReport.reporter_id == user_id
    if session_ids:
        report_filter = or_(report_filter, AIResponseReport.session_id.in_(session_ids))
    await db.execute(delete(AIResponseReport).where(report_filter))
    await db.execute(
        update(AIResponseReport)
        .where(AIResponseReport.resolved_by == user_id)
        .values(resolved_by=None)
    )
    if session_ids:
        await db.execute(delete(Score).where(Score.session_id.in_(session_ids)))
        await db.execute(delete(Message).where(Message.session_id.in_(session_ids)))
        await db.execute(delete(Session).where(Session.id.in_(session_ids)))
    await db.execute(delete(Enrollment).where(Enrollment.user_id == user_id))
    await db.execute(delete(AIConsent).where(AIConsent.user_id == user_id))

    user.email = _placeholder_email(user_id)
    user.display_name = None
    user.fcm_token = None
    user.push_enabled = False
    user.quiet_hours_start = None
    user.quiet_hours_end = None
    user.deleted_at = datetime.now(timezone.utc)
    firebase_uid = user.google_uid
    await db.commit()

    # Post-commit cleanup: best effort, none of it can resurrect data.
    if pending_task_ids:
        from app.celery_app import celery

        for task_id in pending_task_ids:
            try:
                await run_in_threadpool(celery.control.revoke, task_id)
            except Exception:
                logger.warning("account deletion: could not revoke task for user=%s", user_id)
    if redis is not None:
        try:
            await auth_service.revoke_all_refresh_tokens(user_id, redis)
        except Exception:
            logger.warning("account deletion: refresh-token revoke failed for user=%s", user_id)
        for course_id in course_ids:
            await invalidate(redis, summary_key(user_id, course_id))
            await invalidate(redis, class_summary_key(course_id))

    if await delete_firebase_identity(user_id, firebase_uid, db):
        return True
    from app.tasks.account_deletion import retry_firebase_deletion

    try:
        retry_firebase_deletion.apply_async(args=[str(user_id)], countdown=60)
    except Exception:
        # The hourly sweep (sweep_pending_firebase_deletions) still picks it up.
        logger.error("account deletion: could not queue Firebase retry for user=%s", user_id)
    return False


async def delete_firebase_identity(user_id: uuid.UUID, firebase_uid: str, db: AsyncSession) -> bool:
    """Delete the Firebase user; on success mark the tombstone's UID as deleted."""
    if firebase_uid.startswith(DELETED_UID_PREFIX):
        return True
    try:
        await run_in_threadpool(firebase_auth.delete_user, firebase_uid)
    except firebase_auth.UserNotFoundError:
        pass  # already gone — treat as success
    except Exception:
        logger.error("account deletion: Firebase delete failed for user=%s; will retry", user_id)
        return False
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(google_uid=f"{DELETED_UID_PREFIX}{user_id}")
    )
    await db.commit()
    return True


async def pending_firebase_deletions(db: AsyncSession) -> list[tuple[uuid.UUID, str]]:
    rows = (
        await db.execute(
            select(User.id, User.google_uid).where(
                User.deleted_at.is_not(None),
                User.google_uid.not_like(f"{DELETED_UID_PREFIX}%"),
            )
        )
    ).all()
    return [(r.id, r.google_uid) for r in rows]
