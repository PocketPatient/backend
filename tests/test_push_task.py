from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")


# --- _get_fcm_token async helper ---

async def test_get_fcm_token_returns_token_when_set(student, db_session):
    from app.tasks.push_notifications import _get_fcm_token

    stu, _ = student
    stu.fcm_token = "real-device-token"
    db_session.add(stu)
    await db_session.commit()

    # Patch AsyncSessionLocal inside the task module to use the test session.
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.push_notifications.AsyncSessionLocal", return_value=ctx):
        token = await _get_fcm_token(str(stu.id))

    assert token == "real-device-token"


async def test_get_fcm_token_returns_none_when_unset(student, db_session):
    from app.tasks.push_notifications import _get_fcm_token

    stu, _ = student
    # fcm_token is None by default

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.push_notifications.AsyncSessionLocal", return_value=ctx):
        token = await _get_fcm_token(str(stu.id))

    assert token is None


# --- send_push Celery task (sync — patches asyncio.run) ---

def test_send_push_skips_when_no_token():
    with patch("app.tasks.push_notifications.run_task_async") as mock_run, \
         patch("app.tasks.push_notifications._get_fcm_token", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_run.return_value = None  # no token

        from app.tasks.push_notifications import send_push

        send_push.apply(args=["user-id", "Title", "Body", {"type": "test"}])

        mock_ps.send_push_notification.assert_not_called()


def test_send_push_calls_push_service_with_token():
    with patch("app.tasks.push_notifications.run_task_async") as mock_run, \
         patch("app.tasks.push_notifications._get_fcm_token", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_run.return_value = "device-token-xyz"

        from app.tasks.push_notifications import send_push

        send_push.apply(args=["user-id", "Title", "Body", {"type": "new_case", "session_id": "s1"}])

        mock_ps.send_push_notification.assert_called_once_with(
            "device-token-xyz",
            "Title",
            "Body",
            {"type": "new_case", "session_id": "s1"},
        )


def test_send_push_retries_on_exception():
    with patch("app.tasks.push_notifications.run_task_async") as mock_run, \
         patch("app.tasks.push_notifications._get_fcm_token", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_run.return_value = "token"
        mock_ps.send_push_notification.side_effect = Exception("FCM unavailable")

        from app.tasks.push_notifications import send_push
        from celery.exceptions import Retry

        # apply() with throw=True surfaces Celery's Retry exception on the first attempt.
        with pytest.raises(Retry):
            send_push.apply(args=["user-id", "t", "b", {}], throw=True)
