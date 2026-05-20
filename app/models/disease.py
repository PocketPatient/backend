from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Disease(Base):
    __tablename__ = "diseases"
    __table_args__ = (
        CheckConstraint("difficulty_tier >= 1 AND difficulty_tier <= 5", name="ck_difficulty_tier_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dsm_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    key_symptoms: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    differentials: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    difficulty_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    speech_style: Mapped[str] = mapped_column(String(100), nullable=False)
    nudge_behavior: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
