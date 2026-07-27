from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.openapi import errors
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.user import User
from app.routers.courses import _make_course_out
from app.schemas.course import CourseOut
from app.schemas.enrollment import EnrolledStudentOut, EnrollmentJoinRequest

router = APIRouter(tags=["enrollments"])


@router.post("/enrollments/join", response_model=CourseOut, summary="Join a course by class code", responses=errors(401, 403, 404, 409, 410, 422, 429))
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


@router.get("/courses/{course_id}/students", response_model=list[EnrolledStudentOut], summary="List enrolled students", responses=errors(401, 403, 404, 429))
async def list_students(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Course not found")

    rows = (
        await db.execute(
            select(User, Enrollment.enrolled_at)
            .join(Enrollment, Enrollment.user_id == User.id)
            .where(Enrollment.course_id == course_id)
        )
    ).all()

    return [
        EnrolledStudentOut(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            enrolled_at=enrolled_at,
        )
        for user, enrolled_at in rows
    ]


@router.delete("/courses/{course_id}/students/{user_id}", status_code=204, summary="Unenroll a student", responses=errors(401, 403, 404, 429))
async def remove_student(
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Course not found")

    enrollment_result = await db.execute(
        select(Enrollment).where(
            Enrollment.course_id == course_id,
            Enrollment.user_id == user_id,
        )
    )
    row = enrollment_result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Student not enrolled in this course")

    await db.delete(row)
    await db.commit()
