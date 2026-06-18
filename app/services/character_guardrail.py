from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message, MessageRole

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
FALLBACK_TEXT = "I don't know, doctor... I just don't feel right"
AI_BREAK_PHRASES = [
    "as an ai",
    "as a language model",
    "i'm a language model",
    "i am a language model",
    "i'm an ai",
    "i am an ai",
    "i don't actually have",
]


def check_character_break(text: str, disease_name: str) -> str | None:
    """Return a violation reason ('ai_break' / 'diagnosis_leak') or None if in character."""
    lowered = text.lower()
    if any(phrase in lowered for phrase in AI_BREAK_PHRASES):
        return "ai_break"
    if disease_name and disease_name.lower() in lowered:
        return "diagnosis_leak"
    return None


def _system_message(session_id: uuid.UUID, content: str) -> Message:
    return Message(
        session_id=session_id,
        role=MessageRole.system,
        content=content,
        sent_at=datetime.now(timezone.utc),
        is_nudge=False,
        token_count=None,
    )


async def generate_in_character(
    generate_fn: Callable[[], Awaitable[str]],
    *,
    disease_name: str,
    db: AsyncSession,
    session_id: uuid.UUID,
) -> str:
    """Generate patient text, regenerating up to MAX_RETRIES on a character break,
    then fall back to a generic in-character line. Logs each regeneration/fallback as
    an internal `system` Message (db.add only — caller commits)."""
    reason: str | None = None
    for attempt in range(MAX_RETRIES + 1):
        text = await generate_fn()
        reason = check_character_break(text, disease_name)
        if reason is None:
            return text
        if attempt < MAX_RETRIES:
            logger.warning(
                "Character break (%s) on session %s attempt %d; regenerating",
                reason,
                session_id,
                attempt + 1,
            )
            db.add(_system_message(session_id, f"[regenerated: {reason}]"))
    logger.warning(
        "Character guardrail fell back on session %s after %d retries (last reason: %s)",
        session_id,
        MAX_RETRIES,
        reason,
    )
    db.add(_system_message(session_id, f"[fallback used: {reason}]"))
    return FALLBACK_TEXT
