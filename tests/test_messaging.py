from __future__ import annotations

from datetime import time, timezone
from unittest.mock import MagicMock

import pytest

from app.models.course import Course
from app.services.messaging import is_within_messaging_window, window_end_utc


def _make_course(start: str, end: str, tz: str = "America/New_York") -> Course:
    course = MagicMock(spec=Course)
    sh, sm = start.split(":")
    eh, em = end.split(":")
    course.msg_window_start = time(int(sh), int(sm))
    course.msg_window_end = time(int(eh), int(em))
    course.msg_timezone = tz
    return course


def test_within_window():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(12, 0)) is True


def test_before_window():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(7, 59)) is False


def test_after_window():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(22, 1)) is False


def test_at_start_boundary():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(8, 0)) is True


def test_at_end_boundary_is_out_half_open():
    # Half-open [start, end): the end minute is OUTSIDE the window.
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(22, 0)) is False


def test_just_before_end_boundary_is_in():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(21, 59)) is True


# --- window_end_utc ---

def test_window_end_utc_converts_local_end_to_utc():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    course = _make_course("08:00", "22:00", tz="America/New_York")
    ny = ZoneInfo("America/New_York")
    now_local = datetime(2026, 7, 1, 12, 0, tzinfo=ny)  # summer, EDT = UTC-4

    result = window_end_utc(course, now_local)

    assert result == datetime(2026, 7, 2, 2, 0, tzinfo=timezone.utc)
