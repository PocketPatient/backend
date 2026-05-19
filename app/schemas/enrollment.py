from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class EnrollmentJoinRequest(BaseModel):
    class_code: str


class EnrolledStudentOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    enrolled_at: datetime

    model_config = {"from_attributes": True}
