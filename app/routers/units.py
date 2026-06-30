from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_role
from app.openapi import errors
from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from app.schemas.unit import DiseaseOut, UnitOut, UnitOutStudent

router = APIRouter(prefix="/courses/{course_id}", tags=["units"])


def _make_disease_out(disease: Disease) -> DiseaseOut:
    return DiseaseOut(
        id=disease.id,
        name=disease.name,
        category=disease.category,
        difficulty_tier=disease.difficulty_tier,
    )


def _make_unit_out(unit: Unit, diseases: list[Disease]) -> UnitOut:
    outs = [_make_disease_out(d) for d in diseases]
    return UnitOut(
        id=unit.id,
        course_id=unit.course_id,
        label=unit.label,
        status=unit.status,
        release_date=unit.release_date,
        disease_count=len(outs),
        diseases=outs,
    )


@router.get("/units", response_model=None, summary="List units in a course", responses=errors(401, 404, 429))
async def list_units(
    course_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == UserRole.professor:
        course = (
            await db.execute(
                select(Course).where(
                    Course.id == course_id, Course.professor_id == current_user.id
                )
            )
        ).scalar_one_or_none()
        if course is None:
            raise HTTPException(status_code=404, detail="Course not found")

        units = (
            await db.execute(select(Unit).where(Unit.course_id == course_id))
        ).scalars().all()

        if not units:
            return []

        unit_ids = [u.id for u in units]
        all_diseases = (
            await db.execute(
                select(Disease).where(
                    Disease.unit_id.in_(unit_ids),
                    Disease.is_active == True,  # noqa: E712
                )
            )
        ).scalars().all()

        diseases_by_unit: dict[uuid.UUID, list[Disease]] = {u.id: [] for u in units}
        for d in all_diseases:
            diseases_by_unit[d.unit_id].append(d)

        return [_make_unit_out(unit, diseases_by_unit[unit.id]) for unit in units]

    else:
        enrolled = (
            await db.execute(
                select(Enrollment).where(
                    Enrollment.course_id == course_id,
                    Enrollment.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if enrolled is None:
            raise HTTPException(status_code=404, detail="Course not found")

        units = (
            await db.execute(
                select(Unit).where(
                    Unit.course_id == course_id, Unit.status == UnitStatus.released
                )
            )
        ).scalars().all()

        if not units:
            return []

        unit_ids = [u.id for u in units]
        counts_rows = (
            await db.execute(
                select(Disease.unit_id, func.count().label("cnt"))
                .where(Disease.unit_id.in_(unit_ids), Disease.is_active == True)  # noqa: E712
                .group_by(Disease.unit_id)
            )
        ).all()
        counts_by_unit: dict[uuid.UUID, int] = {row.unit_id: row.cnt for row in counts_rows}

        return [
            UnitOutStudent(
                id=unit.id,
                label=unit.label,
                status=unit.status,
                release_date=unit.release_date,
                disease_count=counts_by_unit.get(unit.id, 0),
            )
            for unit in units
        ]


async def _get_owned_unit(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> tuple[Unit, list[Disease]]:
    course = (
        await db.execute(
            select(Course).where(
                Course.id == course_id, Course.professor_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    unit = (
        await db.execute(
            select(Unit).where(Unit.id == unit_id, Unit.course_id == course_id)
        )
    ).scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    diseases = (
        await db.execute(
            select(Disease).where(
                Disease.unit_id == unit.id, Disease.is_active == True  # noqa: E712
            )
        )
    ).scalars().all()
    return unit, list(diseases)


@router.put("/units/{unit_id}/release", response_model=UnitOut, summary="Release a unit", responses=errors(401, 404, 429))
async def release_unit(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    unit, diseases = await _get_owned_unit(course_id, unit_id, current_user, db)
    if unit.status != UnitStatus.draft:
        raise HTTPException(status_code=409, detail="Unit is not in draft status")
    unit.status = UnitStatus.released
    unit.release_date = datetime.now(timezone.utc)
    result = _make_unit_out(unit, diseases)  # build before commit — commit expires ORM objects
    await db.commit()
    return result


@router.put("/units/{unit_id}/close", response_model=UnitOut, summary="Close a unit", responses=errors(401, 404, 429))
async def close_unit(
    course_id: uuid.UUID,
    unit_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    unit, diseases = await _get_owned_unit(course_id, unit_id, current_user, db)
    if unit.status != UnitStatus.released:
        raise HTTPException(status_code=409, detail="Unit is not released")
    unit.status = UnitStatus.closed
    result = _make_unit_out(unit, diseases)  # build before commit — commit expires ORM objects
    await db.commit()
    return result


@router.get("/disease-pool", response_model=list[DiseaseOut], summary="List the course disease pool", responses=errors(401, 404, 429))
async def get_disease_pool(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = (
        await db.execute(
            select(Course).where(
                Course.id == course_id, Course.professor_id == current_user.id
            )
        )
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    diseases = (
        await db.execute(
            select(Disease)
            .join(Unit, Disease.unit_id == Unit.id)
            .where(
                Unit.course_id == course_id,
                Unit.status == UnitStatus.released,
                Disease.is_active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    return [_make_disease_out(d) for d in diseases]
