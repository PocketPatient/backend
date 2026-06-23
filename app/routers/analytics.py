from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models.user import User
from app.schemas.analytics import StudentSummary
from app.services.analytics_cache import (
    get_cached_json,
    set_cached_json,
    summary_key,
)
from app.services.analytics_service import get_student_summary

router = APIRouter(prefix="/analytics", tags=["analytics"])


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
