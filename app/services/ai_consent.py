"""AI-disclosure consent: the single enforcement point for every Gemini call.

Every code path that sends a user's case/transcript to Gemini must call
``require_ai_consent`` (request paths) or ``has_active_consent`` (Celery tasks,
which re-check at execution time because consent may be revoked after a task
was queued). The client cannot bypass this: the check is server-side and keyed
on the currently required disclosure version.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai_consent import AIConsent

AI_CONSENT_REQUIRED = "AI_CONSENT_REQUIRED"


async def get_active_consent(db: AsyncSession, user_id: uuid.UUID) -> AIConsent | None:
    return (
        await db.execute(
            select(AIConsent).where(
                AIConsent.user_id == user_id,
                AIConsent.version == settings.ai_consent_version,
                AIConsent.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def has_active_consent(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return await get_active_consent(db, user_id) is not None


async def require_ai_consent(db: AsyncSession, user_id: uuid.UUID) -> None:
    if not await has_active_consent(db, user_id):
        raise HTTPException(
            status_code=403,
            detail={
                "detail": "AI consent required: accept the current AI disclosure first",
                "code": AI_CONSENT_REQUIRED,
                "required_version": settings.ai_consent_version,
            },
        )


async def accept(db: AsyncSession, user_id: uuid.UUID) -> AIConsent:
    """Record acceptance of the current version (idempotent)."""
    existing = await get_active_consent(db, user_id)
    if existing is not None:
        return existing
    consent = AIConsent(
        user_id=user_id,
        version=settings.ai_consent_version,
        accepted_at=datetime.now(timezone.utc),
    )
    db.add(consent)
    await db.commit()
    await db.refresh(consent)
    return consent


async def revoke(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Withdraw consent (all versions). Rows are kept for the audit trail."""
    await db.execute(
        update(AIConsent)
        .where(AIConsent.user_id == user_id, AIConsent.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
