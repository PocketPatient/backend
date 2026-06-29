from __future__ import annotations

import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.score import Score
from app.models.session import Session, SessionStatus
from app.models.unit import Unit
from app.models.user import User
from app.schemas.analytics import (
    CategoryHeatmap,
    CategoryScore,
    ClassSummary,
    CompletedSessionItem,
    FlaggedStudent,
    ResponseTimePoint,
    ScoreBucket,
    ScoreByCase,
    StudentSummary,
    UnitCompletion,
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
                # avg_score averages diagnosed sessions that have a Score row;
                # avg_rt averages all diagnosed sessions. They differ only for the
                # degenerate diagnosed-without-Score case, which diagnose never creates.
                func.avg(Score.total_score)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("avg_score"),
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
                Session.completed_at.asc().nulls_last(),
                Session.started_at.asc(),
                Session.id.asc(),
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
    weak_categories = sorted(
        r.category for r in cat_rows if r.avg_score < WEAK_CATEGORY_THRESHOLD
    )

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


async def list_completed_sessions(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    status: SessionStatus | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[CompletedSessionItem], int]:
    filters = [Session.course_id == course_id]
    if user_id is not None:
        filters.append(Session.user_id == user_id)
    if status is not None:
        filters.append(Session.status == status)

    total = (
        await db.execute(
            select(func.count(Session.id)).where(*filters)
        )
    ).scalar_one()

    rows = (
        await db.execute(
            select(
                Session.id,
                Disease.name,
                Disease.category,
                Score.total_score,
                Session.turn_count,
                Session.started_at,
                Session.completed_at,
                Session.avg_response_latency_sec,
            )
            .join(Disease, Disease.id == Session.disease_id)
            .outerjoin(Score, Score.session_id == Session.id)
            .where(*filters)
            .order_by(
                Session.completed_at.desc().nulls_last(),
                Session.started_at.desc(),
                Session.id.desc(),
            )
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
    ).all()

    items = [
        CompletedSessionItem(
            session_id=r.id,
            disease_name=r.name,
            category=r.category,
            score=r.total_score,
            turn_count=r.turn_count,
            started_at=r.started_at,
            completed_at=r.completed_at,
            avg_response_latency_sec=r.avg_response_latency_sec,
        )
        for r in rows
    ]
    return items, total


# Buckets for score_distribution. The first is inclusive at 0; the rest are
# (lo, hi] so each score lands in exactly one bucket.
SCORE_BUCKETS = [
    ("0-20", 0.0, 20.0),
    ("21-40", 20.0, 40.0),
    ("41-60", 40.0, 60.0),
    ("61-80", 60.0, 80.0),
    ("81-100", 80.0, 100.0),
]


async def get_class_summary(
    course_id: uuid.UUID, db: AsyncSession, bottom_pct: float = 0.2
) -> ClassSummary:
    enrolled = (
        await db.execute(
            select(func.count(Enrollment.id)).where(
                Enrollment.course_id == course_id
            )
        )
    ).scalar_one()

    active = (
        await db.execute(
            select(func.count(func.distinct(Session.user_id))).where(
                Session.course_id == course_id,
                Session.status == SessionStatus.active,
            )
        )
    ).scalar_one()

    total_completed = (
        await db.execute(
            select(func.count(Session.id)).where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
            )
        )
    ).scalar_one()

    avg_class_score = (
        await db.execute(
            select(func.avg(Score.total_score))
            .select_from(Session)
            .join(Score, Score.session_id == Session.id)
            .where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
            )
        )
    ).scalar_one()

    completion_by_unit = await _completion_by_unit(course_id, db)
    score_distribution = await _score_distribution(course_id, db)
    category_heatmap, flagged_students = await _heatmap_and_flagged(
        course_id, db, bottom_pct
    )

    return ClassSummary(
        enrolled_students=enrolled,
        students_with_active_case=active,
        total_completed_cases=total_completed,
        avg_class_score=round(avg_class_score, 1)
        if avg_class_score is not None
        else None,
        completion_by_unit=completion_by_unit,
        score_distribution=score_distribution,
        category_heatmap=category_heatmap,
        flagged_students=flagged_students,
    )


async def _completion_by_unit(
    course_id: uuid.UUID, db: AsyncSession
) -> list[UnitCompletion]:
    units = (
        await db.execute(
            select(Unit.id, Unit.label)
            .where(Unit.course_id == course_id)
            .order_by(Unit.created_at, Unit.label)
        )
    ).all()
    if not units:
        return []
    unit_ids = [u.id for u in units]

    disease_counts = dict(
        (
            await db.execute(
                select(Disease.unit_id, func.count(Disease.id))
                .where(Disease.unit_id.in_(unit_ids))
                .group_by(Disease.unit_id)
            )
        ).all()
    )

    stat_rows = (
        await db.execute(
            select(
                Disease.unit_id,
                func.count(Session.id).label("started"),
                func.count(Session.id)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("diagnosed"),
                func.avg(Score.total_score)
                .filter(Session.status == SessionStatus.diagnosed)
                .label("avg_score"),
            )
            .select_from(Session)
            .join(Disease, Disease.id == Session.disease_id)
            .outerjoin(Score, Score.session_id == Session.id)
            .where(Session.course_id == course_id)
            .group_by(Disease.unit_id)
        )
    ).all()
    stats = {r.unit_id: r for r in stat_rows}

    out = []
    for u in units:
        st = stats.get(u.id)
        out.append(
            UnitCompletion(
                unit_label=u.label,
                total_diseases=disease_counts.get(u.id, 0),
                total_cases_started=st.started if st else 0,
                total_diagnosed=st.diagnosed if st else 0,
                avg_score=round(st.avg_score, 1)
                if st and st.avg_score is not None
                else None,
            )
        )
    return out


async def _score_distribution(
    course_id: uuid.UUID, db: AsyncSession
) -> list[ScoreBucket]:
    scores = (
        await db.execute(
            select(Score.total_score)
            .select_from(Session)
            .join(Score, Score.session_id == Session.id)
            .where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
                Score.total_score.isnot(None),
            )
        )
    ).scalars().all()

    counts = {label: 0 for label, _, _ in SCORE_BUCKETS}
    for s in scores:
        for label, lo, hi in SCORE_BUCKETS:
            if (s >= lo if lo == 0.0 else s > lo) and s <= hi:
                counts[label] += 1
                break
    return [
        ScoreBucket(range=label, count=counts[label])
        for label, _, _ in SCORE_BUCKETS
    ]


async def _heatmap_and_flagged(
    course_id: uuid.UUID, db: AsyncSession, bottom_pct: float
) -> tuple[CategoryHeatmap, list[FlaggedStudent]]:
    rows = (
        await db.execute(
            select(User.email, Disease.category, Score.total_score)
            .select_from(Session)
            .join(Score, Score.session_id == Session.id)
            .join(Disease, Disease.id == Session.disease_id)
            .join(User, User.id == Session.user_id)
            .join(
                Enrollment,
                and_(
                    Enrollment.user_id == Session.user_id,
                    Enrollment.course_id == course_id,
                ),
            )
            .where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
                Score.total_score.isnot(None),
            )
        )
    ).all()

    cell: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_student: dict[str, list[float]] = defaultdict(list)
    for email, category, score in rows:
        cell[(email, category)].append(score)
        per_student[email].append(score)

    students = sorted(per_student.keys())
    categories = sorted({cat for _, cat in cell.keys()})
    matrix: list[list[float | None]] = []
    for email in students:
        row: list[float | None] = []
        for cat in categories:
            vals = cell.get((email, cat))
            row.append(round(sum(vals) / len(vals), 1) if vals else None)
        matrix.append(row)
    heatmap = CategoryHeatmap(
        students=students, categories=categories, scores=matrix
    )

    avgs = [
        (email, round(sum(v) / len(v), 1), len(v))
        for email, v in per_student.items()
    ]
    avgs.sort(key=lambda t: (t[1], t[0]))
    k = math.ceil(len(avgs) * bottom_pct) if avgs else 0
    flagged = [
        FlaggedStudent(email=e, avg_score=a, completed_cases=c)
        for e, a, c in avgs[:k]
    ]
    return heatmap, flagged


async def get_export_rows(course_id: uuid.UUID, db: AsyncSession):
    return (
        await db.execute(
            select(
                User.email,
                User.display_name,
                Disease.name,
                Disease.category,
                Score.total_score,
                Session.avg_response_latency_sec,
                Session.turn_count,
                Session.completed_at,
            )
            .select_from(Session)
            .join(Score, Score.session_id == Session.id)
            .join(Disease, Disease.id == Session.disease_id)
            .join(User, User.id == Session.user_id)
            .join(
                Enrollment,
                and_(
                    Enrollment.user_id == Session.user_id,
                    Enrollment.course_id == course_id,
                ),
            )
            .where(
                Session.course_id == course_id,
                Session.status == SessionStatus.diagnosed,
            )
            .order_by(
                User.email.asc(),
                Session.completed_at.asc().nulls_last(),
                Session.started_at.asc(),
                Session.id.asc(),
            )
        )
    ).all()
