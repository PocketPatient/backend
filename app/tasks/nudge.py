from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.tasks._run import run_task_async
from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.services.context_window import count_tokens
from app.services.llm_gateway import gateway, patient_identity
from app.tasks.push_notifications import send_push

logger = logging.getLogger(__name__)

_MIN_NUDGE_GAP_HOURS = 6        # smallest configured cadence (the "high" tier)
_FIRST_NUDGE_SILENCE_HOURS = 24
_FREQUENCY_HOURS = {"high": 6, "medium": 24, "low": 48}
_DEFAULT_FREQUENCY_HOURS = 24


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def _fetch_eligible_session_ids(db: AsyncSession) -> list[uuid.UUID]:
    """Active sessions whose latest message is from the patient and at least
    _MIN_NUDGE_GAP_HOURS old — the smallest configured cadence. Selecting at
    this minimum (rather than the 24h first-nudge threshold) keeps every
    cadence tier independently reachable; the precise per-session gate runs
    in _maybe_send_nudge."""
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
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None or session.status != SessionStatus.active:
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
    frequency_hours = _FREQUENCY_HOURS.get(
        disease.nudge_behavior.get("frequency"), _DEFAULT_FREQUENCY_HOURS
    )

    now = datetime.now(timezone.utc)
    if last_nudge is None:
        eligible_to_send = (now - _aware(last_message.sent_at)) >= timedelta(hours=_FIRST_NUDGE_SILENCE_HOURS)
    else:
        eligible_to_send = (now - _aware(last_nudge.sent_at)) >= timedelta(hours=frequency_hours)

    if not eligible_to_send:
        return

    hours = round((now - _aware(last_message.sent_at)).total_seconds() / 3600)
    patient_name, patient_age, _ = patient_identity(session.id.int)
    text = await gateway.generate_nudge_message(disease, patient_name, patient_age, hours)

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
