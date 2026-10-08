from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import account_deletion
from app.tasks._run import run_task_async

logger = logging.getLogger(__name__)


async def _retry_one(user_id: uuid.UUID) -> bool:
    async with AsyncSessionLocal() as db:
        uid = (
            await db.execute(select(User.google_uid).where(User.id == user_id))
        ).scalar_one_or_none()
        if uid is None:
            return True
        return await account_deletion.delete_firebase_identity(user_id, uid, db)


@celery.task(bind=True, max_retries=6)
def retry_firebase_deletion(self, user_id: str) -> None:
    """Retry deleting a deleted account's Firebase identity (backoff 1m..32m)."""
    if run_task_async(_retry_one(uuid.UUID(user_id))):
        return
    logger.error(json.dumps({"event": "firebase-deletion-failed", "user_id": user_id,
                             "attempt": self.request.retries + 1}))
    if self.request.retries < self.max_retries:
        raise self.retry(countdown=60 * 2 ** self.request.retries)
    # Out of fast retries; the hourly sweep keeps trying.


async def _sweep() -> int:
    async with AsyncSessionLocal() as db:
        pending = await account_deletion.pending_firebase_deletions(db)
        remaining = 0
        for user_id, uid in pending:
            if not await account_deletion.delete_firebase_identity(user_id, uid, db):
                remaining += 1
        return remaining


@celery.task
def sweep_pending_firebase_deletions() -> None:
    """Hourly safety net: no deleted account may keep a live Firebase identity."""
    remaining = run_task_async(_sweep())
    if remaining:
        logger.error(json.dumps({"event": "firebase-deletion-pending", "count": remaining}))
