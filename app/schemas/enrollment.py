from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class EnrollmentJoinRequest(BaseModel):
    class_code: str

    @field_validator("class_code", mode="before")
    @classmethod
    def validate_class_code(cls, v: str) -> str:
        if not isinstance(v, str):
            raise ValueError("class_code must be a string")
        v = v.strip().upper()
        if len(v) != 6 or not v.isalnum():
            raise ValueError("class_code must be exactly 6 alphanumeric characters")
        return v


class EnrolledStudentOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    enrolled_at: datetime

    model_config = {"from_attributes": True}
