from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class UserRole(str, Enum):
    student = "student"
    professor = "professor"
    admin = "admin"


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: UserRole | None
    is_verified: bool | None
    display_name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
