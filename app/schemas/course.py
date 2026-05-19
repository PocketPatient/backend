from __future__ import annotations

import uuid
from datetime import datetime, time

from pydantic import BaseModel


class CourseCreate(BaseModel):
    title: str
    semester: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = None
    semester: str | None = None
    msg_window_start: time | None = None
    msg_window_end: time | None = None
    msg_timezone: str | None = None


class CourseOut(BaseModel):
    id: uuid.UUID
    title: str
    professor_id: uuid.UUID
    class_code: str
    semester: str | None
    is_active: bool
    msg_window_start: time
    msg_window_end: time
    msg_timezone: str
    created_at: datetime
    student_count: int

    model_config = {"from_attributes": True}
