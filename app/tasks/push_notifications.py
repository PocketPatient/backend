from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from firebase_admin import messaging
from sqlalchemy import select, update

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import push_service
from app.tasks._run import run_task_async

logger = logging.getLogger(__name__)

# Quiet hours are stored as LOCAL wall-clock times but there is no per-user
# timezone column, so we interpret them in a single app-wide timezone (the
# Rutgers campus tz). TODO(follow-up): add a per-user timezone and use it here —
# this single-campus assumption is wrong for students in other timezones.
APP_TIMEZONE = ZoneInfo("America/New_York")


@dataclass
class _PushState:
    token: str | None
    push_enabled: bool
    quiet_hours_start: time | None
    quiet_hours_end: time | None


async def _get_push_state(user_id: str) -> _PushState:
    """Fetch the device token and notification preferences in one round-trip.

    Defaults (push enabled, no quiet window) are returned when the user is
    missing or the id is malformed, so a bad id never silently suppresses pushes
    for other reasons."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return _PushState(None, True, None, None)
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(
                    User.fcm_token,
                    User.push_enabled,
                    User.quiet_hours_start,
                    User.quiet_hours_end,
                ).where(User.id == uid)
            )
        ).one_or_none()
    if row is None:
        return _PushState(None, True, None, None)
    return _PushState(
        row.fcm_token, row.push_enabled, row.quiet_hours_start, row.quiet_hours_end
    )


async def _clear_fcm_token(user_id: str) -> None:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == uid).values(fcm_token=None))
        await db.commit()


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def send_push(self, user_id: str, title: str, body: str, data: dict[str, str]):
    state = run_task_async(_get_push_state(user_id))
    if not state.token or not state.push_enabled:
        # No device, or the user opted out — drop silently.
        return

    if state.quiet_hours_start is not None and state.quiet_hours_end is not None:
        # Compare against local wall-clock time, since quiet hours are stored as
        # local times (see APP_TIMEZONE). next_window_open then reschedules to the
        # correct local instant (the returned datetime is tz-aware).
        now = datetime.now(APP_TIMEZONE)
        if push_service.is_within_quiet_hours(
            now, state.quiet_hours_start, state.quiet_hours_end
        ):
            # Defer delivery until the quiet window closes instead of dropping.
            deliver_at = push_service.next_window_open(now, state.quiet_hours_end)
            send_push.apply_async(args=[user_id, title, body, data], eta=deliver_at)
            return

    try:
        push_service.send_push_notification(state.token, title, body, data)
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
        # Token is dead — retrying is pointless. Clear it; the client re-registers
        # via PUT /users/me/fcm-token on next app open.
        run_task_async(_clear_fcm_token(user_id))
        logger.warning(json.dumps({"event": "fcm-token-stale", "user_id": user_id}))
        return
    except Exception as exc:
        raise self.retry(exc=exc)
