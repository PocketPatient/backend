from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pathlib import PurePath

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models.course import Course
from app.models.disease import Disease
from app.models.disease_document import DiseaseDocument
from app.models.unit import Unit, UnitStatus
from app.models.user import User
from app.schemas.disease_document import (
    DiffSummary,
    DiseaseDocumentConfirmResult,
    DiseaseDocumentPreview,
    ParseErrorOut,
    UnitPreview,
)
from app.services import disease_parser, file_storage

router = APIRouter(
    prefix="/courses/{course_id}/disease-document",
    tags=["disease-documents"],
)

_SUPPORTED_EXTENSIONS = {"json", "csv"}


async def _get_owned_course(
    course_id: uuid.UUID, current_user: User, db: AsyncSession
) -> Course:
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


def _extract_extension(filename: str | None) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    ext = PurePath(filename).suffix.lower().lstrip(".")
    if ext not in _SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail=f"unsupported file extension: {ext!r} (expected .json or .csv)"
        )
    return ext


@router.post("", response_model=DiseaseDocumentPreview)
async def upload_disease_document(
    course_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = await _get_owned_course(course_id, current_user, db)
    ext = _extract_extension(file.filename)
    raw = await file.read()

    # Parse first — pure function, cheap, fails fast before any side effects.
    result = disease_parser.parse(file.filename, raw)

    max_version = (
        await db.execute(
            select(func.coalesce(func.max(DiseaseDocument.version), 0)).where(
                DiseaseDocument.course_id == course.id
            )
        )
    ).scalar_one()
    next_version = max_version + 1

    file_url = file_storage.save_upload(course.id, next_version, ext, raw)

    doc = DiseaseDocument(
        course_id=course.id,
        uploaded_by=current_user.id,
        file_url=file_url,
        version=next_version,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return DiseaseDocumentPreview(
        document_id=doc.id,
        version=doc.version,
        units=[
            UnitPreview(
                label=u.label,
                disease_count=len(u.diseases),
                diseases=[d.name for d in u.diseases],
            )
            for u in result.units
        ],
        errors=[ParseErrorOut(location=e.location, message=e.message) for e in result.errors],
    )


@router.post("/confirm", response_model=DiseaseDocumentConfirmResult)
async def confirm_disease_document(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = await _get_owned_course(course_id, current_user, db)

    doc_result = await db.execute(
        select(DiseaseDocument)
        .where(DiseaseDocument.course_id == course.id, DiseaseDocument.parsed_at.is_(None))
        .order_by(DiseaseDocument.uploaded_at.desc())
        .limit(1)
        .with_for_update()
    )
    doc = doc_result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="No pending upload to confirm")

    if not file_storage.upload_exists(doc.file_url):
        raise HTTPException(status_code=410, detail="Upload file expired, please re-upload")

    raw = file_storage.read_upload(doc.file_url)
    filename = f"doc.{doc.file_url.rsplit('.', 1)[-1]}"
    parse_result = disease_parser.parse(filename, raw)

    if parse_result.errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "File has parse errors; cannot confirm",
                "errors": [
                    {"location": e.location, "message": e.message} for e in parse_result.errors
                ],
            },
        )

    existing_units = (
        await db.execute(select(Unit).where(Unit.course_id == course.id).with_for_update())
    ).scalars().all()
    if any(u.status == UnitStatus.released for u in existing_units):
        raise HTTPException(
            status_code=409,
            detail="Close all released units before re-uploading",
        )

    for u in existing_units:
        await db.delete(u)
    await db.flush()

    units_created = 0
    diseases_created = 0
    units_added = []
    for parsed_unit in parse_result.units:
        unit = Unit(course_id=course.id, label=parsed_unit.label)
        db.add(unit)
        await db.flush()
        units_created += 1
        units_added.append(parsed_unit.label)
        for d in parsed_unit.diseases:
            db.add(
                Disease(
                    unit_id=unit.id,
                    name=d.name,
                    dsm_code=d.dsm_code,
                    category=d.category,
                    key_symptoms=d.key_symptoms,
                    differentials=d.differentials,
                    difficulty_tier=d.difficulty_tier,
                    speech_style=d.speech_style,
                    nudge_behavior=d.nudge_behavior,
                )
            )
            diseases_created += 1

    doc.parsed_at = datetime.now(timezone.utc)
    await db.commit()

    return DiseaseDocumentConfirmResult(
        document_id=doc.id,
        version=doc.version,
        units_created=units_created,
        diseases_created=diseases_created,
        diff=DiffSummary(
            units_added=units_added,
            units_orphaned=[],
            diseases_added=diseases_created,
            diseases_modified=0,
            diseases_removed=0,
        ),
    )
