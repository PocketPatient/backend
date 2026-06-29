from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")


def _state(token="device-token-xyz", push_enabled=True, qs=None, qe=None):
    from app.tasks.push_notifications import _PushState

    return _PushState(token, push_enabled, qs, qe)


# --- _get_push_state async helper ---

async def test_get_push_state_returns_token_and_prefs(student, db_session):
    from app.tasks.push_notifications import _get_push_state

    stu, _ = student
    stu.fcm_token = "real-device-token"
    db_session.add(stu)
    await db_session.commit()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.push_notifications.AsyncSessionLocal", return_value=ctx):
        state = await _get_push_state(str(stu.id))

    assert state.token == "real-device-token"
    assert state.push_enabled is True
    assert state.quiet_hours_start is None


async def test_get_push_state_returns_none_token_when_unset(student, db_session):
    from app.tasks.push_notifications import _get_push_state

    stu, _ = student
    # fcm_token is None by default

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.push_notifications.AsyncSessionLocal", return_value=ctx):
        state = await _get_push_state(str(stu.id))

    assert state.token is None


# --- send_push Celery task (sync — patches run_task_async) ---

def test_send_push_skips_when_no_token():
    with patch("app.tasks.push_notifications.run_task_async") as mock_run, \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_run.return_value = _state(token=None)

        from app.tasks.push_notifications import send_push

        send_push.apply(args=["user-id", "Title", "Body", {"type": "test"}])

        mock_ps.send_push_notification.assert_not_called()


def test_send_push_calls_push_service_with_token():
    with patch("app.tasks.push_notifications.run_task_async") as mock_run, \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_run.return_value = _state(token="device-token-xyz")

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
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_run.return_value = _state(token="token")
        mock_ps.send_push_notification.side_effect = Exception("FCM unavailable")

        from app.tasks.push_notifications import send_push
        from celery.exceptions import Retry

        # apply() with throw=True surfaces Celery's Retry exception on the first attempt.
        with pytest.raises(Retry):
            send_push.apply(args=["user-id", "t", "b", {}], throw=True)


def test_send_push_clears_token_and_does_not_retry_on_unregistered():
    from firebase_admin import messaging

    with patch("app.tasks.push_notifications.run_task_async") as mock_run, \
         patch("app.tasks.push_notifications.push_service") as mock_ps, \
         patch("app.tasks.push_notifications._clear_fcm_token",
               MagicMock(return_value="clear-coro")):
        # 1st run_task_async -> push state; 2nd -> clear-token coroutine.
        mock_run.side_effect = [_state(token="device-token"), None]
        mock_ps.send_push_notification.side_effect = messaging.UnregisteredError("gone")

        from app.tasks.push_notifications import _clear_fcm_token, send_push

        # No Retry raised — returns normally.
        send_push.apply(args=["user-id", "t", "b", {}], throw=True)

        _clear_fcm_token.assert_called_once_with("user-id")
