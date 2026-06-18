from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery
from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.services.character_guardrail import generate_in_character
from app.services.context_window import count_tokens
from app.services.llm_gateway import gateway, patient_identity

_DELAY_RANGES_SEC = {
    "pressured":    (0, 0),
    "flat":         (3600, 4 * 3600),
    "tangential":   (15 * 60, 60 * 60),
    "disorganized": (0, 30 * 60),
}
_DEFAULT_DELAY_RANGE_SEC = (5 * 60, 30 * 60)


def _reply_delay_seconds(speech_style: str) -> float:
    lo, hi = _DELAY_RANGES_SEC.get(speech_style, _DEFAULT_DELAY_RANGE_SEC)
    if lo == hi:
        return lo
    return random.uniform(lo, hi)


def avg_student_latency(messages: list[Message]) -> float | None:
    latencies = [
        m.response_latency_sec
        for m in messages
        if m.role == MessageRole.student and m.response_latency_sec is not None
    ]
    if not latencies:
        return None
    return sum(latencies) / len(latencies)


async def _avg_student_latency_sql(session_id: uuid.UUID, db: AsyncSession) -> float | None:
    """SQL-aggregate equivalent of avg_student_latency for the per-message hot path —
    avoids loading the full transcript just to take a mean."""
    result = await db.execute(
        select(func.avg(Message.response_latency_sec)).where(
            Message.session_id == session_id,
            Message.role == MessageRole.student,
            Message.response_latency_sec.is_not(None),
        )
    )
    avg = result.scalar_one_or_none()
    return float(avg) if avg is not None else None


async def _get_disease_pool(course_id: uuid.UUID, db: AsyncSession) -> list[Disease]:
    result = await db.execute(
        select(Disease)
        .join(Unit, Disease.unit_id == Unit.id)
        .where(
            Unit.course_id == course_id,
            Unit.status == UnitStatus.released,
            Disease.is_active == True,  # noqa: E712
        )
    )
    return list(result.scalars().all())


async def create_new_session(
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[Session, Message]:
    diseases = await _get_disease_pool(course_id, db)
    if not diseases:
        raise HTTPException(status_code=422, detail="No diseases available in the course pool")

    disease = random.choice(diseases)
    session = Session(
        disease_id=disease.id,
        user_id=user_id,
        course_id=course_id,
        started_at=datetime.now(timezone.utc),
        status=SessionStatus.active,
        turn_count=0,
    )
    db.add(session)
    await db.flush()

    patient_name, patient_age = patient_identity(session.id.int)
    opening_text = await generate_in_character(
        lambda: gateway.generate_opening_message(disease, patient_name, patient_age),
        disease_name=disease.name,
        db=db,
        session_id=session.id,
    )

    now = datetime.now(timezone.utc)
    message = Message(
        session_id=session.id,
        role=MessageRole.patient,
        content=opening_text,
        sent_at=now,
        is_nudge=False,
        token_count=count_tokens(opening_text),
    )
    db.add(message)
    await db.commit()
    await db.refresh(session)
    await db.refresh(message)
    return session, message


async def get_active_session(
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    db: AsyncSession,
) -> Session | None:
    result = await db.execute(
        select(Session).where(
            Session.user_id == user_id,
            Session.course_id == course_id,
            Session.status == SessionStatus.active,
        )
    )
    return result.scalar_one_or_none()


async def get_session_messages(session_id: uuid.UUID, db: AsyncSession) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.sent_at.asc(), Message.created_at.asc())
    )
    return list(result.scalars().all())


async def handle_student_message(
    session: Session,
    student_content: str,
    instant: bool,
    db: AsyncSession,
) -> Message:
    """Save the student's message, refresh latency stats, and (re)schedule the
    delayed bot reply. Reply generation itself happens in generate_and_send_reply."""
    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()

    last_patient_msg = (
        await db.execute(
            select(Message)
            .where(Message.session_id == session.id, Message.role == MessageRole.patient)
            .order_by(Message.sent_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    latency: float | None = None
    if last_patient_msg is not None:
        last_sent = last_patient_msg.sent_at
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        latency = (now - last_sent).total_seconds()

    student_msg = Message(
        session_id=session.id,
        role=MessageRole.student,
        content=student_content,
        sent_at=now,
        is_nudge=False,
        response_latency_sec=latency,
        token_count=count_tokens(student_content),
    )
    db.add(student_msg)
    await db.flush()

    session.avg_response_latency_sec = await _avg_student_latency_sql(session.id, db)

    if session.pending_reply_task_id:
        try:
            celery.control.revoke(session.pending_reply_task_id)
        except Exception:
            pass

    task_id = str(uuid.uuid4())
    session.pending_reply_task_id = task_id
    db.add(session)
    await db.commit()
    await db.refresh(student_msg)

    from app.tasks.bot_reply import generate_and_send_reply

    dispatch_kwargs: dict = {"args": [str(session.id)], "task_id": task_id}
    if not instant:
        delay = _reply_delay_seconds(disease.speech_style)
        dispatch_kwargs["eta"] = datetime.now(timezone.utc) + timedelta(seconds=delay)
    generate_and_send_reply.apply_async(**dispatch_kwargs)

    return student_msg
