from __future__ import annotations

import uuid

from pydantic import BaseModel


class UnitPreview(BaseModel):
    label: str
    disease_count: int
    diseases: list[str]


class ParseErrorOut(BaseModel):
    location: str
    message: str


class DiffSummary(BaseModel):
    units_added: list[str]
    units_orphaned: list[str]
    diseases_added: int
    diseases_modified: int
    diseases_removed: int


class DiseaseDocumentPreview(BaseModel):
    document_id: uuid.UUID
    version: int
    units: list[UnitPreview]
    errors: list[ParseErrorOut]
    diff: DiffSummary | None = None


class DiseaseDocumentConfirmResult(BaseModel):
    document_id: uuid.UUID
    version: int
    units_created: int
    diseases_created: int
    diff: DiffSummary
