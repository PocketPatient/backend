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
    professor_id: uuid.UUID = Field(description="ID of the professor who owns the course.")
    class_code: str = Field(description="6-char uppercase join code (no 0/O/1/I/L).")
    semester: str | None
    is_active: bool
    msg_window_start: time
    msg_window_end: time
    msg_timezone: str
    created_at: datetime
    student_count: int = Field(description="Number of enrolled students (computed).")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "title": "Intro to Clinical Psychiatry",
                "professor_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "class_code": "BRT4K9",
                "semester": "Fall 2026",
                "is_active": True,
                "msg_window_start": "08:00:00",
                "msg_window_end": "22:00:00",
                "msg_timezone": "America/New_York",
                "created_at": "2026-09-01T14:30:00Z",
                "student_count": 24,
            }
        },
    }
