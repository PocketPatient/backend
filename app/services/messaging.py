from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.models.course import Course


def is_within_messaging_window(course: Course, _now: time | None = None) -> bool:
    """Return True if the current time (in the course's timezone) is within the
    messaging window.

    The window is HALF-OPEN: ``start <= now < end``. The start minute is
    in-window; the end minute is out. This is the single source of truth for the
    window check — case initiation calls it too, so the two can never diverge at
    the boundary minute.

    The _now parameter exists for testing — pass it to override the current
    time-of-day. In production, omit it and the real wall-clock time is used.
    """
    if _now is None:
        tz = ZoneInfo(course.msg_timezone)
        _now = datetime.now(tz).time()
    return course.msg_window_start <= _now < course.msg_window_end


def window_end_utc(course: Course, now_local: datetime) -> datetime:
    """The course's messaging-window end for ``now_local``'s date, as a UTC
    datetime. Used by case initiation to bound the random initiation ETA to the
    remaining window. ``now_local`` must be tz-aware in the course's timezone.
    """
    tz = ZoneInfo(course.msg_timezone)
    window_end_naive = datetime.combine(now_local.date(), course.msg_window_end)
    return window_end_naive.replace(tzinfo=tz).astimezone(timezone.utc)
