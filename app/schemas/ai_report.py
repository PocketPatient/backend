from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.ai_report import AIReportReason, AIReportStatus


class AIReportCreate(BaseModel):
    message_id: uuid.UUID = Field(description="ID of the AI (patient) message being reported. The transcript itself is never sent.")
    reason: AIReportReason
    comment: str | None = Field(default=None, max_length=1000, description="Optional free-text context (max 1000 chars).")


class AIReportOut(BaseModel):
    id: uuid.UUID
    message_id: uuid.UUID
    session_id: uuid.UUID
    reason: AIReportReason
    status: AIReportStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class AIReportAdminOut(AIReportOut):
    reporter_id: uuid.UUID
    comment: str | None
    message_content: str = Field(description="Current text of the reported message (read from the transcript, not stored on the report).")
    resolved_at: datetime | None
    resolved_by: uuid.UUID | None
    resolution_note: str | None


class AIReportResolve(BaseModel):
    resolution_note: str = Field(min_length=1, max_length=2000, description="What was done (e.g. guardrail/prompt change, no action).")
