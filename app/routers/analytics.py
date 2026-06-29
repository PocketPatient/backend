from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.user import User
from app.schemas.analytics import ClassSummary, StudentDrilldown, StudentSummary
from app.services.analytics_cache import (
    class_summary_key,
    get_cached_json,
    set_cached_json,
    summary_key,
)
from app.services.analytics_service import (
    get_class_summary,
    get_export_rows,
    get_student_summary,
    list_completed_sessions,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _require_owned_course(
    course_id: uuid.UUID, user: User, db: AsyncSession
) -> Course:
    course = (
        await db.execute(
            select(Course).where(
                Course.id == course_id, Course.professor_id == user.id
            )
        )
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


@router.get("/student/summary", response_model=StudentSummary)
async def student_summary(
    course_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
) -> StudentSummary:
    redis = getattr(request.app.state, "redis", None)
    key = summary_key(current_user.id, course_id)

    cached = await get_cached_json(redis, key)
    if cached is not None:
        return StudentSummary.model_validate(cached)

    summary = await get_student_summary(current_user.id, course_id, db)
    await set_cached_json(redis, key, summary.model_dump(mode="json"))
    return summary


@router.get("/professor/class-summary", response_model=ClassSummary)
async def professor_class_summary(
    course_id: uuid.UUID,
    request: Request,
    bottom_pct: float = Query(0.2, gt=0, le=1),
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
) -> ClassSummary:
    await _require_owned_course(course_id, current_user, db)
    redis = getattr(request.app.state, "redis", None)
    # Only the default percentile is cached, so invalidation stays simple.
    use_cache = bottom_pct == 0.2
    key = class_summary_key(course_id)
    if use_cache:
        cached = await get_cached_json(redis, key)
        if cached is not None:
            return ClassSummary.model_validate(cached)
    summary = await get_class_summary(course_id, db, bottom_pct)
    if use_cache:
        await set_cached_json(redis, key, summary.model_dump(mode="json"))
    return summary


@router.get("/professor/student/{user_id}", response_model=StudentDrilldown)
async def professor_student_drilldown(
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
) -> StudentDrilldown:
    await _require_owned_course(course_id, current_user, db)
    enrolled = (
        await db.execute(
            select(Enrollment.id).where(
                Enrollment.user_id == user_id,
                Enrollment.course_id == course_id,
            )
        )
    ).scalar_one_or_none()
    if enrolled is None:
        raise HTTPException(status_code=404, detail="Student not found")

    redis = getattr(request.app.state, "redis", None)
    key = summary_key(user_id, course_id)
    cached = await get_cached_json(redis, key)
    if cached is not None:
        summary = StudentSummary.model_validate(cached)
    else:
        summary = await get_student_summary(user_id, course_id, db)
        await set_cached_json(redis, key, summary.model_dump(mode="json"))

    items, total = await list_completed_sessions(
        db, course_id=course_id, user_id=user_id, page=page, page_size=page_size
    )
    return StudentDrilldown(**summary.model_dump(), sessions=items, total=total)


@router.get("/professor/export")
async def professor_export(
    course_id: uuid.UUID,
    format: str = "csv",
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await _require_owned_course(course_id, current_user, db)
    if format != "csv":
        raise HTTPException(status_code=400, detail="Unsupported format")

    rows = await get_export_rows(course_id, db)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "student_email",
            "student_name",
            "case_number",
            "disease_name",
            "category",
            "score",
            "response_time_avg",
            "turns",
            "date_completed",
        ]
    )
    counts: dict[str, int] = {}
    for r in rows:
        n = counts.get(r.email, 0) + 1
        counts[r.email] = n
        writer.writerow(
            [
                r.email,
                r.display_name or "",
                n,
                r.name,
                r.category,
                r.total_score if r.total_score is not None else "",
                r.avg_response_latency_sec
                if r.avg_response_latency_sec is not None
                else "",
                r.turn_count,
                r.completed_at.isoformat() if r.completed_at else "",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="grades_{course_id}.csv"'
        },
    )
