from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit
from app.models.user import User, UserRole
from app.schemas.session import (
    DiagnosisCreate,
    DiagnosisResult,
    MessageCreate,
    MessageOut,
    RevealOut,
    ScoreOut,
    SessionCreate,
    SessionOut,
)
from app.services.grading_service import generate_diagnosis_hint, grade_diagnosis
from app.services.session_service import (
    create_new_session,
    get_active_session,
    get_session_messages,
    send_student_message_and_get_reply,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _session_out(
    session: Session,
    messages: list[Message],
    score: ScoreOut | None = None,
    reveal: RevealOut | None = None,
) -> SessionOut:
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
        score=score,
        reveal=reveal,
    )


async def _load_reveal(
    session: Session, db: AsyncSession
) -> tuple[ScoreOut | None, RevealOut | None]:
    if session.status != SessionStatus.diagnosed:
        return None, None
    score_row = (
        await db.execute(select(Score).where(Score.session_id == session.id))
    ).scalar_one_or_none()
    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()
    unit = (
        await db.execute(select(Unit).where(Unit.id == disease.unit_id))
    ).scalar_one()
    # A diagnosed session normally has a Score row; if one is somehow missing we
    # still reveal the disease/unit (the answer is no longer secret) with score=None.
    score_out = ScoreOut.model_validate(score_row) if score_row is not None else None
    reveal = RevealOut(
        disease_name=disease.name,
        dsm_code=disease.dsm_code,
        unit_label=unit.label,
    )
    return score_out, reveal


@router.get("/active", response_model=SessionOut)
async def get_active_session_endpoint(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    session = await get_active_session(current_user.id, course_id, db)
    if session is None:
        raise HTTPException(status_code=404, detail="No active session")
    messages = await get_session_messages(session.id, db)
    return _session_out(session, messages)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if current_user.role == UserRole.student:
        if session.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        # Professor: must own the course this session belongs to
        course = (
            await db.execute(
                select(Course).where(
                    Course.id == session.course_id,
                    Course.professor_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if course is None:
            raise HTTPException(status_code=404, detail="Session not found")

    messages = await get_session_messages(session.id, db)
    score_out, reveal = await _load_reveal(session, db)
    return _session_out(session, messages, score_out, reveal)


@router.post("/{session_id}/messages", response_model=MessageOut, status_code=201)
async def send_message(
    session_id: uuid.UUID,
    body: MessageCreate,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    session = (
        await db.execute(
            select(Session).where(
                Session.id == session_id,
                Session.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.active:
        raise HTTPException(status_code=409, detail="Session is not active")

    patient_reply = await send_student_message_and_get_reply(session, body.content, db)
    return MessageOut(
        id=patient_reply.id,
        role=patient_reply.role,
        content=patient_reply.content,
        sent_at=patient_reply.sent_at,
        response_latency_sec=patient_reply.response_latency_sec,
    )


@router.post("/{session_id}/diagnose", response_model=DiagnosisResult)
async def diagnose(
    session_id: uuid.UUID,
    body: DiagnosisCreate,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
) -> DiagnosisResult:
    session = (
        await db.execute(
            select(Session).where(
                Session.id == session_id,
                Session.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.active:
        raise HTTPException(status_code=409, detail="Session is not active")

    score = await grade_diagnosis(session, body, db)
    disease = (
        await db.execute(select(Disease).where(Disease.id == session.disease_id))
    ).scalar_one()

    if not score.is_correct:
        hint = await generate_diagnosis_hint(body.primary_dx, disease.name)
        # Persist only the refreshed avg latency (mutated inside grade_diagnosis);
        # the Score was built but never db.add-ed, so no score row is written and
        # the session stays active. Safe because no ORM relationship cascades to Score.
        await db.commit()
        return DiagnosisResult(correct=False, hint=hint)

    db.add(score)
    session.status = SessionStatus.diagnosed
    session.completed_at = datetime.now(timezone.utc)
    db.add(session)
    await db.commit()
    await db.refresh(score)

    unit = (
        await db.execute(select(Unit).where(Unit.id == disease.unit_id))
    ).scalar_one()
    return DiagnosisResult(
        correct=True,
        score=ScoreOut.model_validate(score),
        reveal=RevealOut(
            disease_name=disease.name,
            dsm_code=disease.dsm_code,
            unit_label=unit.label,
        ),
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
