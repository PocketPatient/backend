from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.message import MessageRole
from app.models.session import SessionStatus


class SessionCreate(BaseModel):
    course_id: uuid.UUID


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    sent_at: datetime
    response_latency_sec: float | None

    model_config = {"from_attributes": True}


class ScoreOut(BaseModel):
    primary_dx: str
    differentials: list[str]
    justification: str | None
    is_correct: bool | None
    rubric_score: float | None
    response_time_score: float | None
    total_score: float | None
    feedback_text: str | None
    graded_at: datetime | None

    model_config = {"from_attributes": True}


class RevealOut(BaseModel):
    disease_name: str
    dsm_code: str | None
    unit_label: str

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):
    id: uuid.UUID = Field(description="Unique session identifier.")
    disease_id: uuid.UUID = Field(description="ID of the disease case assigned to this session.")
    course_id: uuid.UUID = Field(description="ID of the course this session belongs to.")
    status: SessionStatus = Field(description="Current session status: active, diagnosed, or abandoned.")
    turn_count: int = Field(description="Number of message turns exchanged so far.")
    started_at: datetime = Field(description="ISO-8601 timestamp when the session was started.")
    messages: list[MessageOut]
    score: ScoreOut | None = None
    reveal: RevealOut | None = None

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "disease_id": "4cb96e75-6828-5673-c4gd-3d074g77bgb7",
                "course_id": "5dc07f86-7939-6784-d5he-4e185h88chc8",
                "status": "active",
                "turn_count": 4,
                "started_at": "2026-09-15T09:00:00Z",
                "messages": [
                    {
                        "id": "6ed18g97-8a4a-7895-e6if-5f296i99didi9",
                        "role": "student",
                        "content": "Hello, how are you feeling today?",
                        "sent_at": "2026-09-15T09:00:05Z",
                        "response_latency_sec": None,
                    }
                ],
                "score": None,
                "reveal": None,
            }
        },
    }


class DiagnosisCreate(BaseModel):
    primary_dx: str = Field(min_length=1, max_length=255)
    differentials: list[str] = Field(default_factory=list, max_length=3)
    justification: str = Field(min_length=50, max_length=2000)


class DiagnosisResult(BaseModel):
    correct: bool
    score: ScoreOut | None = None
    reveal: RevealOut | None = None
    hint: str | None = None

    model_config = {"from_attributes": True}


class SessionStats(BaseModel):
    total_turns: int
    total_duration_sec: float | None
    avg_response_latency_sec: float | None
    student_msg_len_avg: float | None
    student_msg_len_min: int | None
    student_msg_len_max: int | None
    topic_coverage_score: float
    topics_covered: list[str]
    topics_missed: list[str]
