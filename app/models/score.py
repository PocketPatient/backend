from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    primary_dx: Mapped[str] = mapped_column(String(255), nullable=False)
    differentials: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rubric_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    response_time_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
