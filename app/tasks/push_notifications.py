from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import push_service


async def _get_fcm_token(user_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User.fcm_token).where(User.id == uuid.UUID(user_id))
        )
        return result.scalar_one_or_none()


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def send_push(
    self,
    user_id: str,
    title: str,
    body: str,
    data: dict[str, str],
) -> None:
    token = asyncio.run(_get_fcm_token(user_id))
    if not token:
        return
    try:
        push_service.send_push_notification(token, title, body, data)
    except Exception as exc:
        raise self.retry(exc=exc)
