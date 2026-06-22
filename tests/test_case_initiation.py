from __future__ import annotations

import uuid
from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}


@pytest_asyncio.fixture
async def ci_setup(professor, student, db_session):
    """Course with a released unit, enrolled student, no active session."""
    prof, _ = professor
    stu, _ = student
    course = Course(
        title="CI Course",
        professor_id=prof.id,
        class_code="CIC001",
        is_active=True,
        msg_window_start=time(0, 0),   # midnight — window is always open in tests
        msg_window_end=time(23, 59),
        msg_timezone="UTC",
    )
    db_session.add(course)
    await db_session.flush()

    unit = Unit(
        course_id=course.id,
        label="Unit 1",
        status=UnitStatus.released,
        release_date=datetime.now(timezone.utc),
    )
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id,
        name="MDD",
        category="Mood",
        key_symptoms=["low mood"],
        differentials=["GAD"],
        difficulty_tier=2,
        speech_style="flat",
        nudge_behavior=_NUDGE,
    )
    db_session.add(disease)
    await db_session.flush()

    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(stu)
    return stu, course, disease


# --- _fetch_eligible_pairs ---

async def test_fetch_eligible_pairs_returns_eligible_student(ci_setup, db_session):
    from app.tasks.case_initiation import _fetch_eligible_pairs

    stu, course, _ = ci_setup

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx):
        pairs = await _fetch_eligible_pairs()

    assert any(uid == stu.id and cid == course.id for uid, cid, *_ in pairs)


async def test_fetch_eligible_pairs_skips_student_with_active_session(
    ci_setup, db_session
):
    from app.tasks.case_initiation import _fetch_eligible_pairs

    stu, course, disease = ci_setup
    session = Session(
        disease_id=disease.id,
        user_id=stu.id,
        course_id=course.id,
        started_at=datetime.now(timezone.utc),
        status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx):
        pairs = await _fetch_eligible_pairs()

    assert not any(uid == stu.id for uid, cid, *_ in pairs)


async def test_fetch_eligible_pairs_skips_outside_window(ci_setup, db_session):
    from app.tasks.case_initiation import _fetch_eligible_pairs

    _, course, _ = ci_setup
    # Close the window so no time qualifies
    course.msg_window_start = time(0, 0)
    course.msg_window_end = time(0, 1)  # 00:00–00:01 — almost never matches
    db_session.add(course)
    await db_session.commit()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    # Force "now" to be outside the window (e.g. 12:00 UTC)
    fixed_now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx), \
         patch("app.tasks.case_initiation.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.combine = datetime.combine
        pairs = await _fetch_eligible_pairs()

    assert pairs == []


# --- _check_and_create ---

async def test_check_and_create_creates_session_when_none_exists(
    ci_setup, db_session
):
    from app.tasks.case_initiation import _check_and_create

    stu, course, _ = ci_setup

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.id = uuid.uuid4()

    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx), \
         patch("app.tasks.case_initiation.session_service") as mock_ss:
        mock_ss.create_new_session = AsyncMock(return_value=(mock_session, MagicMock()))
        result = await _check_and_create(str(stu.id), str(course.id))

    assert result == mock_session.id
    mock_ss.create_new_session.assert_called_once()


async def test_check_and_create_returns_none_when_session_exists(
    ci_setup, db_session
):
    from app.tasks.case_initiation import _check_and_create

    stu, course, disease = ci_setup
    session = Session(
        disease_id=disease.id,
        user_id=stu.id,
        course_id=course.id,
        started_at=datetime.now(timezone.utc),
        status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx), \
         patch("app.tasks.case_initiation.session_service") as mock_ss:
        result = await _check_and_create(str(stu.id), str(course.id))

    assert result is None
    mock_ss.create_new_session.assert_not_called()


# --- check_and_initiate_cases (sync task, mock asyncio.run + Redis) ---

def test_check_and_initiate_cases_dispatches_for_eligible_students():
    user_id = uuid.uuid4()
    course_id = uuid.uuid4()
    window_end = datetime(2026, 6, 4, 22, 0, 0, tzinfo=timezone.utc)

    with patch("app.tasks.case_initiation.run_task_async") as mock_run, \
         patch("app.tasks.case_initiation._fetch_eligible_pairs", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.case_initiation.sync_redis") as mock_redis_mod, \
         patch("app.tasks.case_initiation.initiate_case") as mock_task:
        mock_run.return_value = [(user_id, course_id, window_end, "UTC")]

        mock_r = MagicMock()
        mock_r.set.return_value = True  # key was newly set (not a duplicate)
        mock_redis_mod.from_url.return_value = mock_r

        from app.tasks.case_initiation import check_and_initiate_cases
        check_and_initiate_cases()

        mock_task.apply_async.assert_called_once()
        call_kwargs = mock_task.apply_async.call_args
        assert call_kwargs.kwargs["args"] == [str(user_id), str(course_id)]


def test_check_and_initiate_cases_skips_duplicate_via_redis():
    user_id = uuid.uuid4()
    course_id = uuid.uuid4()
    window_end = datetime(2026, 6, 4, 22, 0, 0, tzinfo=timezone.utc)

    with patch("app.tasks.case_initiation.run_task_async") as mock_run, \
         patch("app.tasks.case_initiation._fetch_eligible_pairs", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.case_initiation.sync_redis") as mock_redis_mod, \
         patch("app.tasks.case_initiation.initiate_case") as mock_task:
        mock_run.return_value = [(user_id, course_id, window_end, "UTC")]

        mock_r = MagicMock()
        mock_r.set.return_value = False  # key already exists — duplicate
        mock_redis_mod.from_url.return_value = mock_r

        from app.tasks.case_initiation import check_and_initiate_cases
        check_and_initiate_cases()

        mock_task.apply_async.assert_not_called()


# --- initiate_case (sync task) ---

def test_initiate_case_creates_session_and_sends_push():
    session_id = uuid.uuid4()
    user_id = str(uuid.uuid4())

    with patch("app.tasks.case_initiation.run_task_async") as mock_run, \
         patch("app.tasks.case_initiation._check_and_create", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.case_initiation.send_push") as mock_push:
        mock_run.return_value = session_id

        from app.tasks.case_initiation import initiate_case
        initiate_case(user_id, str(uuid.uuid4()))

        mock_push.delay.assert_called_once()
        push_args = mock_push.delay.call_args.args
        assert push_args[0] == user_id
        assert push_args[3]["type"] == "new_case"
        assert push_args[3]["session_id"] == str(session_id)


def test_initiate_case_skips_push_when_session_already_exists():
    with patch("app.tasks.case_initiation.run_task_async") as mock_run, \
         patch("app.tasks.case_initiation._check_and_create", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.case_initiation.send_push") as mock_push:
        mock_run.return_value = None  # already had a session

        from app.tasks.case_initiation import initiate_case
        initiate_case(str(uuid.uuid4()), str(uuid.uuid4()))

        mock_push.delay.assert_not_called()


def test_initiate_case_skips_on_http_exception():
    from fastapi import HTTPException

    with patch("app.tasks.case_initiation.run_task_async") as mock_run, \
         patch("app.tasks.case_initiation._check_and_create", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.case_initiation.send_push") as mock_push:
        mock_run.side_effect = HTTPException(status_code=422, detail="No diseases")

        from app.tasks.case_initiation import initiate_case
        initiate_case(str(uuid.uuid4()), str(uuid.uuid4()))

        mock_push.delay.assert_not_called()


def test_initiate_case_skips_on_integrity_error():
    from sqlalchemy.exc import IntegrityError

    with patch("app.tasks.case_initiation.run_task_async") as mock_run, \
         patch("app.tasks.case_initiation._check_and_create", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.case_initiation.send_push") as mock_push:
        mock_run.side_effect = IntegrityError("", {}, Exception())

        from app.tasks.case_initiation import initiate_case
        initiate_case(str(uuid.uuid4()), str(uuid.uuid4()))

        mock_push.delay.assert_not_called()
