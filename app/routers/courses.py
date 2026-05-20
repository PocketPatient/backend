from __future__ import annotations

import random
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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


@router.get("", response_model=list[CourseOut])
async def list_courses(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sq = _student_count_subquery()
    if current_user.role == UserRole.professor:
        stmt = (
            select(Course, sq.label("student_count"))
            .where(Course.professor_id == current_user.id)
        )
    else:
        enrolled_sq = select(Enrollment.course_id).where(Enrollment.user_id == current_user.id)
        stmt = (
            select(Course, sq.label("student_count"))
            .where(Course.id.in_(enrolled_sq))
        )
    rows = (await db.execute(stmt)).all()
    return [_make_course_out(course, count) for course, count in rows]


@router.get("/{course_id}", response_model=CourseOut)
async def get_course(
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sq = _student_count_subquery()
    result = await db.execute(
        select(Course, sq.label("student_count")).where(Course.id == course_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Course not found")
    course, count = row

    if current_user.role == UserRole.professor:
        if course.professor_id != current_user.id:
            raise HTTPException(status_code=404, detail="Course not found")
    else:
        enrolled = await db.execute(
            select(Enrollment).where(
                Enrollment.course_id == course_id,
                Enrollment.user_id == current_user.id,
            )
        )
        if enrolled.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Course not found")

    return _make_course_out(course, count)


@router.put("/{course_id}", response_model=CourseOut)
async def update_course(
    course_id: uuid.UUID,
    body: CourseUpdate,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(course, field, value)

    await db.commit()
    await db.refresh(course)
    count = await _count_students(db, course.id)
    return _make_course_out(course, count)


@router.delete("/{course_id}/deactivate", response_model=CourseOut)
async def deactivate_course(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    course.is_active = False
    await db.commit()
    await db.refresh(course)
    count = await _count_students(db, course.id)
    return _make_course_out(course, count)


@router.post("", status_code=201, response_model=CourseOut)
async def create_course(
    body: CourseCreate,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    for _ in range(10):
        code = _generate_class_code()
        course = Course(
            title=body.title,
            professor_id=current_user.id,
            class_code=code,
            semester=body.semester,
        )
        db.add(course)
        try:
            await db.commit()
            await db.refresh(course)
            return _make_course_out(course, 0)
        except IntegrityError:
            await db.rollback()
    raise HTTPException(status_code=500, detail="Failed to generate unique class code")
