from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class UserRole(str, Enum):
    student = "student"
    professor = "professor"
    admin = "admin"


class UserOut(BaseModel):
    id: uuid.UUID = Field(description="Unique user identifier.")
    email: str
    role: UserRole | None = Field(description="Role assigned to the user: student, professor, or admin.")
    is_verified: bool | None = Field(description="Whether the user account has been verified.")
    display_name: str | None
    created_at: datetime = Field(description="ISO-8601 timestamp when the account was created.")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "email": "student@example.edu",
                "role": "student",
                "is_verified": True,
                "display_name": "Jane Smith",
                "created_at": "2026-09-01T10:00:00Z",
            }
        },
    }
