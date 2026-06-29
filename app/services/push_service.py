from __future__ import annotations

from datetime import datetime, time, timedelta

from firebase_admin import messaging


def is_within_quiet_hours(now: datetime, start: time, end: time) -> bool:
    """True if `now`'s time-of-day falls in the [start, end) quiet window.

    The window is half-open: the start boundary is quiet, the end boundary is
    not (so a notification re-queued for the end time delivers immediately).
    A window where start > end wraps past midnight (e.g. 22:00-08:00); an empty
    window where start == end is never quiet.
    """
    if start == end:
        return False
    t = now.time()
    if start < end:
        return start <= t < end
    return t >= start or t < end


def next_window_open(now: datetime, end: time) -> datetime:
    """The next datetime at the `end` time at or after `now` (quiet hours close).

    Used to schedule a delayed delivery so notifications raised during quiet
    hours fire the moment the window opens.
    """
    candidate = datetime.combine(now.date(), end, tzinfo=now.tzinfo)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def send_push_notification(
    token: str, title: str, body: str, data: dict[str, str]
) -> None:
    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body),
        data=data,
    )
    messaging.send(message)
