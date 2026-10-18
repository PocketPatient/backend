from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.tasks._run import run_task_async
from app.models.course import Course
from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.services.ai_consent import has_active_consent
from app.services.character_guardrail import generate_in_character
from app.services.context_window import count_tokens
from app.services.llm_gateway import gateway, patient_identity
from app.services.messaging import is_within_messaging_window
from app.tasks.push_notifications import send_push

logger = logging.getLogger(__name__)

# Follow-up cadence by the disease's nudge_behavior["frequency"] tier. A patient
# who reached out checks back within hours, sooner the more urgent they are.
# Unknown tiers fall back to "medium". Nudges also only go out inside the
# course's messaging window (see _maybe_send_nudge).
_FIRST_NUDGE_HOURS = {"high": 2, "medium": 4, "low": 8}   # silence before the first
_FREQUENCY_HOURS = {"high": 4, "medium": 12, "low": 24}   # gap between later nudges
_DEFAULT_FREQUENCY = "medium"
_MIN_NUDGE_GAP_HOURS = min(_FIRST_NUDGE_HOURS.values())   # candidate-query cutoff


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _fetch_eligible_session_ids(db: AsyncSession) -> list[uuid.UUID]:
    """Active sessions whose latest message is from the patient and at least
    _MIN_NUDGE_GAP_HOURS old — the smallest configured cadence. Selecting at
    this minimum keeps every cadence tier independently reachable; the precise
    per-session gate runs in _maybe_send_nudge."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_MIN_NUDGE_GAP_HOURS)
    latest_msgs = (
        select(Message.session_id, Message.role, Message.sent_at)
        .join(Session, Session.id == Message.session_id)
        .where(Session.status == SessionStatus.active)
        .distinct(Message.session_id)
        .order_by(Message.session_id, Message.sent_at.desc())
        .subquery()
    )
    result = await db.execute(
        select(latest_msgs.c.session_id).where(
            latest_msgs.c.role == MessageRole.patient,
            latest_msgs.c.sent_at <= cutoff,
        )
    )
    return [row[0] for row in result.all()]


async def _maybe_send_nudge(session_id: uuid.UUID, db: AsyncSession) -> None:
    # TODO(perf): ~5 sequential queries per session (session, course, last message, last
    # nudge, disease) — an N+1 across the eligible set. Batch/join if the eligible
    # count grows; kept simple here to avoid risking the per-session gating logic.
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None or session.status != SessionStatus.active:
        return
    # Nudges are Gemini-generated: skip students without active AI consent.
    if not await has_active_consent(db, session.user_id):
        return
    # Patients only reach out during the course's messaging window (same rule as
    # case initiation). An overdue nudge simply fires on the first hourly check
    # after the window opens.
    course = (
        await db.execute(select(Course).where(Course.id == session.course_id))
    ).scalar_one()
    if not is_within_messaging_window(course):
        return

    last_message = (
        await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.sent_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last_message is None or last_message.role != MessageRole.patient:
        return

    last_nudge = (
        await db.execute(
            select(Message)
            .where(Message.session_id == session_id, Message.is_nudge == True)  # noqa: E712
            .order_by(Message.sent_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()
    tier = str(disease.nudge_behavior.get("frequency") or "").strip().lower()
    if tier not in _FREQUENCY_HOURS:
        tier = _DEFAULT_FREQUENCY

    now = datetime.now(timezone.utc)
    if last_nudge is None:
        eligible_to_send = (now - _aware(last_message.sent_at)) >= timedelta(hours=_FIRST_NUDGE_HOURS[tier])
    else:
        eligible_to_send = (now - _aware(last_nudge.sent_at)) >= timedelta(hours=_FREQUENCY_HOURS[tier])

    if not eligible_to_send:
        return

    hours = round((now - _aware(last_message.sent_at)).total_seconds() / 3600)
    patient_name, patient_age, _ = patient_identity(session.id.int)
    # Route the nudge through the character guardrail (same as openers/replies) so
    # a nudge that names the diagnosis or breaks character is regenerated/suppressed
    # rather than delivered verbatim.
    text = await generate_in_character(
        lambda: gateway.generate_nudge_message(disease, patient_name, patient_age, hours),
        disease_name=disease.name,
        db=db,
        session_id=session.id,
    )

    db.add(Message(
        session_id=session.id,
        role=MessageRole.patient,
        content=text,
        sent_at=now,
        is_nudge=True,
        token_count=count_tokens(text),
    ))
    await db.commit()

    # Push only after the nudge is durably committed — a push for a Message that
    # later rolled back would notify the student of a reply that never persisted.
    try:
        send_push.delay(
            str(session.user_id),
            "PocketPatient",
            "Your patient replied",
            {"type": "new_message", "session_id": str(session.id)},
        )
    except Exception:
        logger.exception("check_and_send_nudges: send_push.delay failed for session=%s", session.id)


async def _run_nudge_check() -> None:
    async with AsyncSessionLocal() as db:
        session_ids = await _fetch_eligible_session_ids(db)
        for session_id in session_ids:
            # Isolate per-session failures (e.g. a transient LLM error) so one bad
            # session can't abort nudges for every other eligible session this run.
            try:
                await _maybe_send_nudge(session_id, db)
            except Exception:
                await db.rollback()
                logger.exception("check_and_send_nudges: nudge failed for session=%s", session_id)


@celery.task
def check_and_send_nudges() -> None:
    run_task_async(_run_nudge_check())
