from __future__ import annotations

from firebase_admin import messaging


def send_push_notification(
    token: str, title: str, body: str, data: dict[str, str]
) -> None:
    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body),
        data=data,
    )
    messaging.send(message)
