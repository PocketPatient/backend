from __future__ import annotations

import uuid
from datetime import datetime, time
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, String, Time, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserRole(str, PyEnum):
    student = "student"
    professor = "professor"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    google_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[UserRole | None] = mapped_column(Enum(UserRole), nullable=True)
    is_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    fcm_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    push_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Quiet hours are stored as wall-clock times (UTC); both are set together or
    # both null (no quiet window). A window where start > end wraps past midnight.
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)
