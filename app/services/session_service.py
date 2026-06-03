from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.services.llm_gateway import gateway, patient_identity


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
    opening_text = await gateway.generate_opening_message(disease, patient_name, patient_age)

    now = datetime.now(timezone.utc)
    message = Message(
        session_id=session.id,
        role=MessageRole.patient,
        content=opening_text,
        sent_at=now,
        is_nudge=False,
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


async def send_student_message_and_get_reply(
    session: Session,
    student_content: str,
    db: AsyncSession,
) -> Message:
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
    )
    db.add(student_msg)
    # Commit the student's message before calling the LLM so a gateway failure
    # (502/timeout) doesn't roll it back and force the student to retype.
    await db.commit()

    all_messages = await get_session_messages(session.id, db)
    history = [
        {
            "role": "user" if m.role == MessageRole.student else "model",
            "parts": [{"text": m.content}],
        }
        for m in all_messages
    ]

    patient_name, patient_age = patient_identity(session.id.int)
    reply_text = await gateway.generate_patient_message(disease, patient_name, patient_age, history)

    patient_msg = Message(
        session_id=session.id,
        role=MessageRole.patient,
        content=reply_text,
        sent_at=datetime.now(timezone.utc),
        is_nudge=False,
    )
    db.add(patient_msg)

    session.turn_count = (session.turn_count or 0) + 1
    db.add(session)

    await db.commit()
    await db.refresh(patient_msg)
    return patient_msg
