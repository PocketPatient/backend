from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.tasks._run import run_task_async
from app.models.disease import Disease
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.services.character_guardrail import generate_in_character
from app.services.context_window import build_history, count_tokens
from app.services.llm_gateway import gateway, patient_identity
from app.services.session_service import get_session_messages
from app.tasks.push_notifications import send_push

logger = logging.getLogger(__name__)


async def _session_user_id(session_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(Session.user_id).where(Session.id == uuid.UUID(session_id))
            )
        ).scalar_one_or_none()
        return str(row) if row else None


async def _generate_and_send(session_id: str, my_task_id: str) -> None:
    async with AsyncSessionLocal() as db:
        session = (
            await db.execute(select(Session).where(Session.id == uuid.UUID(session_id)))
        ).scalar_one_or_none()

        if (
            session is None
            or session.status != SessionStatus.active
            or session.pending_reply_task_id != my_task_id
        ):
            return

        disease = (
            await db.execute(select(Disease).where(Disease.id == session.disease_id))
        ).scalar_one()
        messages = await get_session_messages(session.id, db)
        history = build_history(messages)

        patient_name, patient_age, _ = patient_identity(session.id.int)
        import time
        _t0 = time.perf_counter()
        reply_text = await generate_in_character(
            lambda: gateway.generate_patient_message(
                disease, patient_name, patient_age, history
            ),
            disease_name=disease.name,
            db=db,
            session_id=session.id,
        )
        logger.info(json.dumps({
            "event": "bot_reply_latency",
            "session_id": session_id,
            "ms": round((time.perf_counter() - _t0) * 1000, 2),
        }))

        db.add(Message(
            session_id=session.id,
            role=MessageRole.patient,
            content=reply_text,
            sent_at=datetime.now(timezone.utc),
            is_nudge=False,
            token_count=count_tokens(reply_text),
        ))
        session.turn_count = (session.turn_count or 0) + 1
        db.add(session)

        result = await db.execute(
            update(Session)
            .where(Session.id == session.id, Session.pending_reply_task_id == my_task_id)
            .values(pending_reply_task_id=None)
        )
        if result.rowcount == 0:
            await db.rollback()
            return

        await db.commit()

    try:
        send_push.delay(
            str(session.user_id),
            "PocketPatient",
            "Your patient replied",
            {"type": "new_message", "session_id": str(session.id)},
        )
    except Exception:
        logger.exception("generate_and_send_reply: send_push.delay failed for session=%s", session_id)


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def generate_and_send_reply(self, session_id: str) -> None:
    try:
        run_task_async(_generate_and_send(session_id, self.request.id))
    except Exception as exc:
        if self.request.retries == 0:
            try:
                user_id = run_task_async(_session_user_id(session_id))
                if user_id:
                    send_push.delay(
                        user_id,
                        "PocketPatient",
                        "Your patient will respond shortly",
                        {"type": "reply_delayed", "session_id": session_id},
                    )
            except Exception:
                logger.exception(
                    "generate_and_send_reply: delayed-reply push failed for session=%s",
                    session_id,
                )
        raise self.retry(exc=exc)
