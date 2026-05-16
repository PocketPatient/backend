from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Time, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    professor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    class_code: Mapped[str] = mapped_column(String(6), unique=True, nullable=False)
    semester: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    msg_window_start: Mapped[time] = mapped_column(Time, server_default="08:00:00")
    msg_window_end: Mapped[time] = mapped_column(Time, server_default="22:00:00")
    msg_timezone: Mapped[str] = mapped_column(String(50), server_default="America/New_York")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
