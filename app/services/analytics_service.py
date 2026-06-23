from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.schemas.analytics import (
    CategoryScore,
    ResponseTimePoint,
    ScoreByCase,
    StudentSummary,
)
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


WEAK_CATEGORY_THRESHOLD = 60.0


async def get_student_summary(
    user_id: uuid.UUID, course_id: uuid.UUID, db: AsyncSession
) -> StudentSummary:
    scope = (Session.user_id == user_id, Session.course_id == course_id)

    # Query 1 — counts + overall averages (SQL aggregation, single row).
    counts_row = (
        await db.execute(
            select(
                func.count(Session.id).label("total"),
                func.count(Session.id)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("completed"),
                func.avg(Score.total_score).label("avg_score"),
                func.avg(Session.avg_response_latency_sec)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("avg_rt"),
            )
            .select_from(Session)
            .outerjoin(Score, Score.session_id == Session.id)
            .where(*scope)
        )
    ).one()

    # Query 2 — per-case rows (diagnosed), ordered by completion.
    case_rows = (
        await db.execute(
            select(
                Session.id,
                Disease.name,
                Disease.category,
                Score.total_score,
                Session.completed_at,
                Session.avg_response_latency_sec,
            )
            .join(Disease, Disease.id == Session.disease_id)
            .join(Score, Score.session_id == Session.id)
            .where(*scope, Session.status == SessionStatus.diagnosed)
            .order_by(
                Session.completed_at.asc().nulls_last(), Session.started_at.asc()
            )
        )
    ).all()

    # Query 3 — per-category aggregation.
    cat_rows = (
        await db.execute(
            select(
                Disease.category,
                func.avg(Score.total_score).label("avg_score"),
                func.count(Score.id).label("count"),
            )
            .select_from(Session)
            .join(Disease, Disease.id == Session.disease_id)
            .join(Score, Score.session_id == Session.id)
            .where(*scope, Session.status == SessionStatus.diagnosed)
            .group_by(Disease.category)
        )
    ).all()

    scores_by_case = [
        ScoreByCase(
            session_id=r.id,
            disease_name=r.name,
            category=r.category,
            score=r.total_score,
            completed_at=r.completed_at,
        )
        for r in case_rows
    ]
    response_time_trend = [
        ResponseTimePoint(case_number=i, avg_latency_sec=r.avg_response_latency_sec)
        for i, r in enumerate(case_rows, start=1)
    ]
    scores_by_category = {
        r.category: CategoryScore(avg_score=round(r.avg_score, 1), count=r.count)
        for r in cat_rows
    }
    weak_categories = [
        r.category for r in cat_rows if r.avg_score < WEAK_CATEGORY_THRESHOLD
    ]

    return StudentSummary(
        total_cases=counts_row.total,
        completed_cases=counts_row.completed,
        avg_score=round(counts_row.avg_score, 1) if counts_row.avg_score is not None else None,
        avg_response_time_sec=round(counts_row.avg_rt, 1) if counts_row.avg_rt is not None else None,
        scores_by_case=scores_by_case,
        scores_by_category=scores_by_category,
        response_time_trend=response_time_trend,
        weak_categories=weak_categories,
    )
