from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.models.course import Course


def is_within_messaging_window(course: Course, _now: time | None = None) -> bool:
    """Return True if the current time (in the course's timezone) is within the messaging window.

    The _now parameter exists for testing — pass it to override the current time.
    In production, omit it and the real wall-clock time is used.
    """
    if _now is None:
        tz = ZoneInfo(course.msg_timezone)
        _now = datetime.now(tz).time()
    return course.msg_window_start <= _now <= course.msg_window_end
