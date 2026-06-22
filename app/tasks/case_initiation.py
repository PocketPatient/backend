from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import redis as sync_redis
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.celery_app import celery
from app.config import settings
from app.tasks._run import run_task_async
from app.database import AsyncSessionLocal
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from app.services import session_service
from app.tasks.push_notifications import send_push

logger = logging.getLogger(__name__)


async def _fetch_eligible_pairs() -> list[tuple[uuid.UUID, uuid.UUID, datetime, str]]:
    """Return (user_id, course_id, window_end_utc) for each eligible student."""
    async with AsyncSessionLocal() as db:
        courses_q = (
            select(Course)
            .join(Unit, Unit.course_id == Course.id)
            .where(Course.is_active == True, Unit.status == UnitStatus.released)  # noqa: E712
            .distinct()
        )
        courses = list((await db.execute(courses_q)).scalars().all())

        now_utc = datetime.now(timezone.utc)
        eligible: list[tuple[uuid.UUID, uuid.UUID, datetime, str]] = []

        for course in courses:
            tz = ZoneInfo(course.msg_timezone)
            now_local = now_utc.astimezone(tz)

            if not (course.msg_window_start <= now_local.time() < course.msg_window_end):
                continue

            window_end_naive = datetime.combine(now_local.date(), course.msg_window_end)
            window_end_local = window_end_naive.replace(tzinfo=tz)
            window_end_utc = window_end_local.astimezone(timezone.utc)

            if window_end_utc <= now_utc:
                continue

            active_session_exists = (
                select(Session.id)
                .where(
                    Session.user_id == User.id,
                    Session.course_id == course.id,
                    Session.status == SessionStatus.active,
                )
                .correlate(User)
                .exists()
            )
            students_q = (
                select(User)
                .join(Enrollment, Enrollment.user_id == User.id)
                .where(
                    Enrollment.course_id == course.id,
                    User.role == UserRole.student,
                    ~active_session_exists,
                )
            )
            students = list((await db.execute(students_q)).scalars().all())

            for student in students:
                eligible.append((student.id, course.id, window_end_utc, course.msg_timezone))

        return eligible


def _random_eta(window_end_utc: datetime) -> datetime:
    now_utc = datetime.now(timezone.utc)
    remaining = (window_end_utc - now_utc).total_seconds()
    if remaining <= 0:
        return now_utc
    delay = random.uniform(0, remaining)
    return now_utc + timedelta(seconds=delay)


@celery.task
def check_and_initiate_cases() -> None:
    pairs = run_task_async(_fetch_eligible_pairs())
    if not pairs:
        return

    r = sync_redis.from_url(settings.redis_url, decode_responses=True)
    try:
        for user_id, course_id, window_end_utc, msg_timezone in pairs:
            local_today = datetime.now(timezone.utc).astimezone(ZoneInfo(msg_timezone)).strftime("%Y-%m-%d")
            dedup_key = f"initiate:{course_id}:{user_id}:{local_today}"
            ttl = max(1, int((window_end_utc - datetime.now(timezone.utc)).total_seconds()))
            if r.set(dedup_key, "1", nx=True, ex=ttl):
                eta = _random_eta(window_end_utc)
                initiate_case.apply_async(
                    args=[str(user_id), str(course_id)], eta=eta
                )
    finally:
        r.close()


async def _check_and_create(
    user_id_str: str, course_id_str: str
) -> uuid.UUID | None:
    """Create a session if the student still has none. Returns session.id or None."""
    user_id = uuid.UUID(user_id_str)
    course_id = uuid.UUID(course_id_str)
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(Session.id).where(
                    Session.user_id == user_id,
                    Session.course_id == course_id,
                    Session.status == SessionStatus.active,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return None
        session, _ = await session_service.create_new_session(user_id, course_id, db)
        return session.id


@celery.task
def initiate_case(user_id: str, course_id: str) -> None:
    try:
        session_id = run_task_async(_check_and_create(user_id, course_id))
    except HTTPException as exc:
        logger.warning(
            "initiate_case: skipping user=%s course=%s — %s",
            user_id, course_id, exc.detail,
        )
        return
    except IntegrityError:
        logger.info(
            "initiate_case: IntegrityError (race) user=%s course=%s", user_id, course_id
        )
        return

    if session_id is None:
        return

    send_push.delay(
        user_id,
        "PocketPatient",
        "A new patient is reaching out to you",
        {"type": "new_case", "session_id": str(session_id)},
    )
