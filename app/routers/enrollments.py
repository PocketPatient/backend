from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.user import User
from app.routers.courses import _make_course_out
from app.schemas.course import CourseOut
from app.schemas.enrollment import EnrolledStudentOut, EnrollmentJoinRequest

router = APIRouter(tags=["enrollments"])


@router.post("/enrollments/join", response_model=CourseOut)
async def join_course(
    body: EnrollmentJoinRequest,
    current_user: User = Depends(require_role("student")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.class_code == body.class_code.upper())
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Invalid class code")
    if not course.is_active:
        raise HTTPException(status_code=410, detail="Course is no longer active")

    existing = await db.execute(
        select(Enrollment).where(
            Enrollment.user_id == current_user.id,
            Enrollment.course_id == course.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Already enrolled in this course")

    enrollment = Enrollment(user_id=current_user.id, course_id=course.id)
    db.add(enrollment)
    await db.commit()

    count_result = await db.execute(
        select(func.count()).select_from(Enrollment).where(Enrollment.course_id == course.id)
    )
    count = count_result.scalar_one()
    return _make_course_out(course, count)
