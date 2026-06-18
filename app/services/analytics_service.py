from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.schemas.session import SessionStats


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def get_session_stats(session_id: uuid.UUID, db: AsyncSession) -> SessionStats:
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise ValueError(f"Session {session_id} not found")

    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()

    student_msgs = list(
        (
            await db.execute(
                select(Message).where(
                    Message.session_id == session_id,
                    Message.role == MessageRole.student,
                )
            )
        ).scalars().all()
    )

    end = session.completed_at or datetime.now(timezone.utc)
    duration = (_aware(end) - _aware(session.started_at)).total_seconds()

    lengths = [len(m.content) for m in student_msgs]
    if lengths:
        len_avg: float | None = sum(lengths) / len(lengths)
        len_min: int | None = min(lengths)
        len_max: int | None = max(lengths)
    else:
        len_avg = len_min = len_max = None

    student_text = " ".join(m.content for m in student_msgs).lower()
    symptoms = disease.key_symptoms or []
    covered = [s for s in symptoms if s.lower() in student_text]
    missed = [s for s in symptoms if s.lower() not in student_text]
    score = len(covered) / len(symptoms) if symptoms else 0.0

    return SessionStats(
        total_turns=session.turn_count,
        total_duration_sec=duration,
        avg_response_latency_sec=session.avg_response_latency_sec,
        student_msg_len_avg=len_avg,
        student_msg_len_min=len_min,
        student_msg_len_max=len_max,
        topic_coverage_score=score,
        topics_covered=covered,
        topics_missed=missed,
    )
