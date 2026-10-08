from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIConsent(Base):
    """Auditable record of a user accepting (and possibly revoking) the AI disclosure.

    One row per acceptance, so history is kept: re-accepting after a revoke or
    accepting a newer disclosure version inserts a new row. A user has active
    consent only if a row for the *currently required* version
    (settings.ai_consent_version) has revoked_at IS NULL.
    """

    __tablename__ = "ai_consents"
    __table_args__ = (
        Index("ix_ai_consents_user_id_version", "user_id", "version"),
        # At most one live (unrevoked) acceptance per user and version.
        Index(
            "uq_ai_consents_active_user_version",
            "user_id",
            "version",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
