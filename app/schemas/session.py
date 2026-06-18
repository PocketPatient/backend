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
    id: uuid.UUID
    disease_id: uuid.UUID
    course_id: uuid.UUID
    status: SessionStatus
    turn_count: int
    started_at: datetime
    messages: list[MessageOut]
    score: ScoreOut | None = None
    reveal: RevealOut | None = None

    model_config = {"from_attributes": True}


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
