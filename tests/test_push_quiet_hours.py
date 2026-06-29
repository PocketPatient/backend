from __future__ import annotations

from datetime import datetime, time, timezone
from unittest.mock import patch

ARGS = ["user-id", "Title", "Body", {"type": "new_message", "session_id": "s1"}]


def _state(token="tok", push_enabled=True, qs=None, qe=None):
    from app.tasks.push_notifications import _PushState

    return _PushState(token, push_enabled, qs, qe)


def test_send_push_dropped_when_push_disabled():
    from app.tasks.push_notifications import send_push

    with patch("app.tasks.push_notifications.run_task_async", return_value=_state(push_enabled=False)), \
         patch("app.tasks.push_notifications.push_service") as mps, \
         patch.object(send_push, "apply_async") as mock_apply:
        send_push.apply(args=ARGS)
        mps.send_push_notification.assert_not_called()
        mock_apply.assert_not_called()


def test_send_push_requeued_during_quiet_hours():
    from app.tasks.push_notifications import send_push

    deliver = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    with patch("app.tasks.push_notifications.run_task_async",
               return_value=_state(qs=time(22, 0), qe=time(8, 0))), \
         patch("app.tasks.push_notifications.push_service") as mps, \
         patch.object(send_push, "apply_async") as mock_apply:
        mps.is_within_quiet_hours.return_value = True
        mps.next_window_open.return_value = deliver

        send_push.apply(args=ARGS)

        mps.send_push_notification.assert_not_called()
        mock_apply.assert_called_once_with(args=ARGS, eta=deliver)


def test_send_push_delivers_outside_quiet_hours():
    from app.tasks.push_notifications import send_push

    with patch("app.tasks.push_notifications.run_task_async",
               return_value=_state(qs=time(22, 0), qe=time(8, 0))), \
         patch("app.tasks.push_notifications.push_service") as mps, \
         patch.object(send_push, "apply_async") as mock_apply:
        mps.is_within_quiet_hours.return_value = False

        send_push.apply(args=ARGS)

        mps.send_push_notification.assert_called_once_with("tok", "Title", "Body", ARGS[3])
        mock_apply.assert_not_called()


def test_send_push_skips_quiet_check_when_no_window_set():
    from app.tasks.push_notifications import send_push

    with patch("app.tasks.push_notifications.run_task_async", return_value=_state()), \
         patch("app.tasks.push_notifications.push_service") as mps:
        send_push.apply(args=ARGS)

        mps.is_within_quiet_hours.assert_not_called()
        mps.send_push_notification.assert_called_once_with("tok", "Title", "Body", ARGS[3])
