from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.ai_report import AIResponseReport
from app.models.course import Course
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.user import User, UserRole
from app.openapi import errors
from app.schemas.ai_report import AIReportCreate, AIReportOut

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post(
    "/ai-response",
    response_model=AIReportOut,
    status_code=201,
    summary="Report an AI-generated patient message",
    responses={200: {"model": AIReportOut, "description": "Already reported by this user; existing report returned."},
               **errors(401, 404, 422, 429)},
)
async def report_ai_response(
    body: AIReportCreate,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AIReportOut:
    # The message must be in a session the caller can see: their own (student)
    # or one in a course they own (professor). Anything else is a 404 so
    # message IDs can't be probed.
    query = select(Message, Session).join(Session, Session.id == Message.session_id).where(
        Message.id == body.message_id
    )
    if current_user.role == UserRole.professor:
        query = query.join(Course, Course.id == Session.course_id).where(
            Course.professor_id == current_user.id
        )
    else:
        query = query.where(Session.user_id == current_user.id)
    row = (await db.execute(query)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Message not found")
    message, session = row
    if message.role != MessageRole.patient:
        raise HTTPException(status_code=422, detail="Only AI-generated patient messages can be reported")

    existing = (
        await db.execute(
            select(AIResponseReport).where(
                AIResponseReport.reporter_id == current_user.id,
                AIResponseReport.message_id == message.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        response.status_code = 200
        return AIReportOut.model_validate(existing)

    report = AIResponseReport(
        reporter_id=current_user.id,
        session_id=session.id,
        message_id=message.id,
        reason=body.reason,
        comment=(body.comment or "").strip() or None,
    )
    db.add(report)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent duplicate submit: return the winner.
        await db.rollback()
        existing = (
            await db.execute(
                select(AIResponseReport).where(
                    AIResponseReport.reporter_id == current_user.id,
                    AIResponseReport.message_id == message.id,
                )
            )
        ).scalar_one()
        response.status_code = 200
        return AIReportOut.model_validate(existing)
    await db.refresh(report)
    return AIReportOut.model_validate(report)
