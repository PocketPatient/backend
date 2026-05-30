from __future__ import annotations

from datetime import time
from unittest.mock import MagicMock

import pytest

from app.models.course import Course
from app.services.messaging import is_within_messaging_window


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


def test_at_end_boundary():
    course = _make_course("08:00", "22:00")
    assert is_within_messaging_window(course, _now=time(22, 0)) is True
