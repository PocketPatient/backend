from __future__ import annotations

import random
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.user import User, UserRole
from app.schemas.course import CourseCreate, CourseOut, CourseUpdate

router = APIRouter(prefix="/courses", tags=["courses"])

_SAFE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _generate_class_code() -> str:
    return "".join(random.choices(_SAFE_CHARS, k=6))


def _make_course_out(course: Course, student_count: int) -> CourseOut:
    return CourseOut(
        id=course.id,
        title=course.title,
        professor_id=course.professor_id,
        class_code=course.class_code,
        semester=course.semester,
        is_active=course.is_active,
        msg_window_start=course.msg_window_start,
        msg_window_end=course.msg_window_end,
        msg_timezone=course.msg_timezone,
        created_at=course.created_at,
        student_count=student_count,
    )


async def _count_students(db: AsyncSession, course_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(Enrollment).where(Enrollment.course_id == course_id)
    )
    return result.scalar_one()


def _student_count_subquery():
    return (
        select(func.count())
        .select_from(Enrollment)
        .where(Enrollment.course_id == Course.id)
        .correlate(Course)
        .scalar_subquery()
    )


@router.post("", status_code=201, response_model=CourseOut)
async def create_course(
    body: CourseCreate,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    for _ in range(10):
        code = _generate_class_code()
        existing = await db.execute(select(Course).where(Course.class_code == code))
        if existing.scalar_one_or_none() is None:
            break
    else:
        raise HTTPException(status_code=500, detail="Failed to generate unique class code")

    course = Course(
        title=body.title,
        professor_id=current_user.id,
        class_code=code,
        semester=body.semester,
    )
    db.add(course)
    await db.commit()
    await db.refresh(course)
    return _make_course_out(course, 0)
