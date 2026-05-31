from __future__ import annotations

import uuid
import zoneinfo
from datetime import datetime, time

from pydantic import BaseModel, Field, field_validator, model_validator


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    semester: str | None = Field(None, max_length=20)

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        return v


class CourseUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=255)
    semester: str | None = Field(None, max_length=20)
    msg_window_start: time | None = None
    msg_window_end: time | None = None
    msg_timezone: str | None = None

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, v: str | None) -> str | None:
        if isinstance(v, str):
            v = v.strip()
        return v

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
