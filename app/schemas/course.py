from __future__ import annotations

import uuid
import zoneinfo
from datetime import datetime, time

from pydantic import BaseModel, field_validator, model_validator


class CourseCreate(BaseModel):
    title: str
    semester: str | None = None


class CourseUpdate(BaseModel):
    title: str | None = None
    semester: str | None = None
    msg_window_start: time | None = None
    msg_window_end: time | None = None
    msg_timezone: str | None = None

    @field_validator("msg_timezone")
    @classmethod
    def check_timezone(cls, v: str | None) -> str | None:
        if v is not None and v not in zoneinfo.available_timezones():
            raise ValueError(f"unknown timezone: {v!r}")
        return v

    @model_validator(mode="after")
    def check_window(self) -> "CourseUpdate":
        if self.msg_window_start is not None and self.msg_window_end is not None:
            if self.msg_window_start >= self.msg_window_end:
                raise ValueError("msg_window_start must be before msg_window_end")
        return self


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
