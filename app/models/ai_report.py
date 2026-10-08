from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIReportReason(str, PyEnum):
    harmful = "harmful"
    inappropriate = "inappropriate"
    inaccurate = "inaccurate"
    out_of_character = "out_of_character"
    other = "other"


class AIReportStatus(str, PyEnum):
    open = "open"
    resolved = "resolved"


class AIResponseReport(Base):
    """A user's report of an AI-generated (patient) message.

    Stores only a reference to the message, never a copy of its text, so the
    report carries no more content than the transcript already holds and is
    removed with it (CASCADE) when the session is deleted.
    """

    __tablename__ = "ai_response_reports"
    __table_args__ = (
        UniqueConstraint("reporter_id", "message_id", name="uq_ai_reports_reporter_message"),
        Index("ix_ai_reports_status_created_at", "status", "created_at"),
        Index("ix_ai_reports_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[AIReportReason] = mapped_column(
        Enum(AIReportReason, name="ai_report_reason"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AIReportStatus] = mapped_column(
        Enum(AIReportStatus, name="ai_report_status"),
        nullable=False,
        server_default="open",
        default=AIReportStatus.open,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(String(2000), nullable=True)
