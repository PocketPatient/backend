from __future__ import annotations

from datetime import datetime, time, timezone

from app.services.push_service import is_within_quiet_hours, next_window_open

UTC = timezone.utc


def _dt(h: int, m: int = 0) -> datetime:
    return datetime(2026, 9, 1, h, m, tzinfo=UTC)


# --- is_within_quiet_hours ---

def test_wrapping_window_is_quiet_late_night():
    # 22:00-08:00 window; 23:30 is inside.
    assert is_within_quiet_hours(_dt(23, 30), time(22, 0), time(8, 0)) is True


def test_wrapping_window_is_quiet_early_morning():
    assert is_within_quiet_hours(_dt(2, 0), time(22, 0), time(8, 0)) is True


def test_wrapping_window_is_not_quiet_midday():
    assert is_within_quiet_hours(_dt(12, 0), time(22, 0), time(8, 0)) is False


def test_window_open_boundary_is_not_quiet():
    # Exactly at end time the window has closed.
    assert is_within_quiet_hours(_dt(8, 0), time(22, 0), time(8, 0)) is False


def test_window_start_boundary_is_quiet():
    assert is_within_quiet_hours(_dt(22, 0), time(22, 0), time(8, 0)) is True


def test_non_wrapping_window():
    # 01:00-06:00 same-day window.
    assert is_within_quiet_hours(_dt(3, 0), time(1, 0), time(6, 0)) is True
    assert is_within_quiet_hours(_dt(7, 0), time(1, 0), time(6, 0)) is False


def test_equal_start_end_is_never_quiet():
    assert is_within_quiet_hours(_dt(5, 0), time(8, 0), time(8, 0)) is False


# --- next_window_open ---

def test_next_window_open_same_day_when_end_ahead():
    # now 02:00, end 08:00 -> today 08:00.
    assert next_window_open(_dt(2, 0), time(8, 0)) == _dt(8, 0)


def test_next_window_open_rolls_to_tomorrow_when_end_passed():
    # now 23:00, end 08:00 -> tomorrow 08:00.
    result = next_window_open(_dt(23, 0), time(8, 0))
    assert result == datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
