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


class DiseaseDocumentPreview(BaseModel):
    document_id: uuid.UUID
    version: int
    units: list[UnitPreview]
    errors: list[ParseErrorOut]


class DiseaseDocumentConfirmResult(BaseModel):
    document_id: uuid.UUID
    version: int
    units_created: int
    diseases_created: int
