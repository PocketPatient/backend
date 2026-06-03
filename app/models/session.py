from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SessionStatus(str, PyEnum):
    active = "active"
    diagnosed = "diagnosed"
    abandoned = "abandoned"


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        # A student may have at most one active session per course at a time.
        Index(
            "uq_one_active_session_per_user_course",
            "user_id",
            "course_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    disease_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diseases.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="session_status"),
        nullable=False,
        server_default="active",
    )
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    avg_response_latency_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
