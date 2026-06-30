from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pathlib import PurePath

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.openapi import errors
from app.models.course import Course
from app.models.disease import Disease
from app.models.disease_document import DiseaseDocument
from app.models.unit import Unit
from app.models.user import User
from app.schemas.disease_document import (
    DiffSummary,
    DiseaseDocumentConfirmResult,
    DiseaseDocumentPreview,
    ParseErrorOut,
    UnitPreview,
)
from app.services import disease_parser, file_storage
from app.services.document_diff import ExistingDisease, compute_diff

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


@router.post("", response_model=DiseaseDocumentPreview, summary="Upload a disease document for preview", responses=errors(400, 401, 403, 404, 422, 429))
async def upload_disease_document(
    course_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = await _get_owned_course(course_id, current_user, db)
    ext = _extract_extension(file.filename)
    raw = await file.read()

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

    diff: DiffSummary | None = None
    if max_version >= 1:
        existing_units = (
            await db.execute(select(Unit).where(Unit.course_id == course.id))
        ).scalars().all()
        existing_unit_labels = [u.label for u in existing_units]
        unit_id_to_label = {u.id: u.label for u in existing_units}

        raw_diseases = (
            await db.execute(
                select(Disease)
                .join(Unit, Disease.unit_id == Unit.id)
                .where(Unit.course_id == course.id, Disease.is_active == True)  # noqa: E712
            )
        ).scalars().all()

        existing_disease_list = [
            ExistingDisease(
                name=d.name,
                unit_label=unit_id_to_label[d.unit_id],
                dsm_code=d.dsm_code,
                category=d.category,
                key_symptoms=d.key_symptoms,
                differentials=d.differentials,
                difficulty_tier=d.difficulty_tier,
                speech_style=d.speech_style,
                nudge_behavior=d.nudge_behavior,
            )
            for d in raw_diseases
        ]

        diff_result = compute_diff(result, existing_unit_labels, existing_disease_list)
        diff = DiffSummary(
            units_added=diff_result.units_added,
            units_orphaned=diff_result.units_orphaned,
            diseases_added=diff_result.diseases_added,
            diseases_modified=diff_result.diseases_modified,
            diseases_removed=diff_result.diseases_removed,
        )

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
        diff=diff,
    )


@router.post("/confirm", response_model=DiseaseDocumentConfirmResult, summary="Confirm a parsed disease document", responses=errors(400, 401, 403, 404, 422, 429))
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

    # Load existing units (all statuses) with a row lock
    existing_units = (
        await db.execute(
            select(Unit).where(Unit.course_id == course.id).with_for_update()
        )
    ).scalars().all()
    existing_unit_map = {u.label: u for u in existing_units}
    unit_id_to_label = {u.id: u.label for u in existing_units}

    # Load active diseases (locked via units join)
    raw_diseases = (
        await db.execute(
            select(Disease)
            .join(Unit, Disease.unit_id == Unit.id)
            .where(Unit.course_id == course.id, Disease.is_active == True)  # noqa: E712
        )
    ).scalars().all()

    existing_disease_map: dict[tuple[str, str], Disease] = {
        (unit_id_to_label[d.unit_id], d.name): d for d in raw_diseases
    }
    existing_disease_list = [
        ExistingDisease(
            name=d.name,
            unit_label=unit_id_to_label[d.unit_id],
            dsm_code=d.dsm_code,
            category=d.category,
            key_symptoms=d.key_symptoms,
            differentials=d.differentials,
            difficulty_tier=d.difficulty_tier,
            speech_style=d.speech_style,
            nudge_behavior=d.nudge_behavior,
        )
        for d in raw_diseases
    ]

    diff_result = compute_diff(
        parse_result,
        list(existing_unit_map.keys()),
        existing_disease_list,
    )

    units_created = 0
    diseases_created = 0
    seen_keys: set[tuple[str, str]] = set()

    for parsed_unit in parse_result.units:
        if parsed_unit.label not in existing_unit_map:
            unit = Unit(course_id=course.id, label=parsed_unit.label)
            db.add(unit)
            await db.flush()
            existing_unit_map[parsed_unit.label] = unit
            units_created += 1

        unit = existing_unit_map[parsed_unit.label]
        for parsed_disease in parsed_unit.diseases:
            key = (parsed_unit.label, parsed_disease.name)
            seen_keys.add(key)
            if key not in existing_disease_map:
                db.add(Disease(
                    unit_id=unit.id,
                    name=parsed_disease.name,
                    dsm_code=parsed_disease.dsm_code,
                    category=parsed_disease.category,
                    key_symptoms=parsed_disease.key_symptoms,
                    differentials=parsed_disease.differentials,
                    difficulty_tier=parsed_disease.difficulty_tier,
                    speech_style=parsed_disease.speech_style,
                    nudge_behavior=parsed_disease.nudge_behavior,
                ))
                diseases_created += 1
            else:
                # Update modified disease in place. Active cases are not affected —
                # a case's system prompt is generated at session creation and cached.
                # Updating a disease here does not change any in-progress case.
                # New cases will use the updated disease data.
                existing_d = existing_disease_map[key]
                existing_d.dsm_code = parsed_disease.dsm_code
                existing_d.category = parsed_disease.category
                existing_d.key_symptoms = parsed_disease.key_symptoms
                existing_d.differentials = parsed_disease.differentials
                existing_d.difficulty_tier = parsed_disease.difficulty_tier
                existing_d.speech_style = parsed_disease.speech_style
                existing_d.nudge_behavior = parsed_disease.nudge_behavior

    # Soft-delete diseases not present in the new file (including orphaned units' diseases)
    for key, disease in existing_disease_map.items():
        if key not in seen_keys:
            disease.is_active = False

    doc.parsed_at = datetime.now(timezone.utc)
    await db.commit()

    return DiseaseDocumentConfirmResult(
        document_id=doc.id,
        version=doc.version,
        units_created=units_created,
        diseases_created=diseases_created,
        diff=DiffSummary(
            units_added=diff_result.units_added,
            units_orphaned=diff_result.units_orphaned,
            diseases_added=diff_result.diseases_added,
            diseases_modified=diff_result.diseases_modified,
            diseases_removed=diff_result.diseases_removed,
        ),
    )
