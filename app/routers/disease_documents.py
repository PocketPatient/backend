from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models.course import Course
from app.models.disease_document import DiseaseDocument
from app.models.user import User
from app.schemas.disease_document import (
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
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
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
