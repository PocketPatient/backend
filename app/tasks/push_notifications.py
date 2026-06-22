from __future__ import annotations

import json
import logging
import uuid

from firebase_admin import messaging
from sqlalchemy import select, update

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import push_service
from app.tasks._run import run_task_async

logger = logging.getLogger(__name__)


async def _get_fcm_token(user_id: str) -> str | None:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User.fcm_token).where(User.id == uid))
        return result.scalar_one_or_none()


async def _clear_fcm_token(user_id: str) -> None:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == uid).values(fcm_token=None))
        await db.commit()


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def send_push(self, user_id, title, body, data):
    token = run_task_async(_get_fcm_token(user_id))
    if not token:
        return
    try:
        push_service.send_push_notification(token, title, body, data)
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
        # Token is dead — retrying is pointless. Clear it; the client re-registers
        # via PUT /users/me/fcm-token on next app open.
        run_task_async(_clear_fcm_token(user_id))
        logger.warning(json.dumps({"event": "fcm-token-stale", "user_id": user_id}))
        return
    except Exception as exc:
        raise self.retry(exc=exc)
