from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_send_push_notification_builds_correct_message():
    with patch("app.services.push_service.messaging") as mock_messaging:
        mock_messaging.Message.return_value = MagicMock()
        mock_messaging.Notification.return_value = MagicMock()

        from app.services.push_service import send_push_notification

        send_push_notification(
            token="device-token-123",
            title="Hello",
            body="World",
            data={"type": "new_case", "session_id": "abc"},
        )

        mock_messaging.Notification.assert_called_once_with(title="Hello", body="World")
        call_kwargs = mock_messaging.Message.call_args.kwargs
        assert call_kwargs["token"] == "device-token-123"
        assert call_kwargs["data"] == {"type": "new_case", "session_id": "abc"}
        assert call_kwargs["notification"] == mock_messaging.Notification.return_value
        mock_messaging.send.assert_called_once()


def test_send_push_notification_propagates_firebase_error():
    from firebase_admin.exceptions import FirebaseError

    with patch("app.services.push_service.messaging") as mock_messaging:
        mock_messaging.send.side_effect = FirebaseError(500, "upstream error")

        from app.services.push_service import send_push_notification

        with pytest.raises(FirebaseError):
            send_push_notification("token", "t", "b", {})
