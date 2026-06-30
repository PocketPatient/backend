from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UnitStatus(str, PyEnum):
    draft = "draft"
    released = "released"
    closed = "closed"


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        Index("ix_units_course_id", "course_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[UnitStatus] = mapped_column(
        Enum(UnitStatus, name="unit_status"), nullable=False, server_default="draft"
    )
    release_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
