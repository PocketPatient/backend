from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.unit import UnitStatus


class DiseaseOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    difficulty_tier: int

    model_config = {"from_attributes": True}


class UnitOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    label: str
    status: UnitStatus
    release_date: datetime | None
    disease_count: int
    diseases: list[DiseaseOut]

    model_config = {"from_attributes": True}


class UnitOutStudent(BaseModel):
    id: uuid.UUID
    label: str
    status: Literal["released"]
    release_date: datetime
    disease_count: int
