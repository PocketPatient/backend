from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.user import User, UserRole
from app.schemas.session import MessageCreate, MessageOut, SessionCreate, SessionOut
from app.services.session_service import (
    create_new_session,
    get_active_session,
    get_session_messages,
    send_student_message_and_get_reply,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _session_out(session: Session, messages: list[Message]) -> SessionOut:
    return SessionOut(
        id=session.id,
        disease_id=session.disease_id,
        course_id=session.course_id,
        status=session.status,
        turn_count=session.turn_count,
        started_at=session.started_at,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                sent_at=m.sent_at,
                response_latency_sec=m.response_latency_sec,
            )
            for m in messages
        ],
    )


@router.post("", response_model=SessionOut, status_code=201)
async def create_session(
    body: SessionCreate,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    enrolled = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.user_id == current_user.id,
                Enrollment.course_id == body.course_id,
            )
        )
    ).scalar_one_or_none()
    if enrolled is None:
        raise HTTPException(status_code=404, detail="Course not found")

    existing = await get_active_session(current_user.id, body.course_id, db)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Active session already exists for this course")

    session, opening_message = await create_new_session(current_user.id, body.course_id, db)
    return _session_out(session, [opening_message])
