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


class SessionOut(BaseModel):
    id: uuid.UUID
    disease_id: uuid.UUID
    course_id: uuid.UUID
    status: SessionStatus
    turn_count: int
    started_at: datetime
    messages: list[MessageOut]

    model_config = {"from_attributes": True}
