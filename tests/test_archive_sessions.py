import uuid
from datetime import datetime, timedelta, timezone

from scripts.archive_old_sessions import cutoff_for, select_old_session_ids


def test_cutoff_for_subtracts_years():
    now = datetime(2026, 6, 29, tzinfo=timezone.utc)
    assert cutoff_for(3, now) == datetime(2023, 6, 29, tzinfo=timezone.utc)


def test_cutoff_for_handles_leap_day():
    # Feb 29 2028 (leap) minus 3 years -> 2025 (non-leap); must clamp to Feb 28.
    leap_day = datetime(2028, 2, 29, tzinfo=timezone.utc)
    assert cutoff_for(3, leap_day) == datetime(2025, 2, 28, tzinfo=timezone.utc)


async def _make_session(db_session, started_at):
    from app.models.user import User
    from app.models.course import Course
    from app.models.unit import Unit
    from app.models.disease import Disease
    from app.models.session import Session as SessionModel

    user = User(google_uid=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.com")
    db_session.add(user)
    await db_session.flush()
    course = Course(title="C", professor_id=user.id, class_code=str(uuid.uuid4())[:6].upper())
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U")
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(
        unit_id=unit.id, name="D", category="c", key_symptoms=[],
        differentials=[], difficulty_tier=1, speech_style="s", nudge_behavior={},
    )
    db_session.add(disease)
    await db_session.flush()
    session = SessionModel(
        disease_id=disease.id, user_id=user.id, course_id=course.id, started_at=started_at,
    )
    db_session.add(session)
    await db_session.commit()
    return session.id


async def test_select_old_session_ids_only_returns_old(clean_tables, db_session):
    now = datetime.now(timezone.utc)
    old_id = await _make_session(db_session, now - timedelta(days=365 * 4))
    await _make_session(db_session, now - timedelta(days=30))
    cutoff = cutoff_for(3, now)
    ids = await select_old_session_ids(db_session, cutoff)
    assert ids == [old_id]
