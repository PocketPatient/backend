import uuid
from datetime import datetime, time, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.models.user import User


@pytest.mark.parametrize(
    "table,index_name",
    [
        ("courses", "ix_courses_professor_id"),
        ("units", "ix_units_course_id"),
        ("sessions", "ix_sessions_disease_id"),
        ("disease_documents", "ix_disease_documents_uploaded_by"),
    ],
)
def test_expected_index_defined(table, index_name):
    idx_names = {ix.name for ix in Base.metadata.tables[table].indexes}
    assert index_name in idx_names, f"{index_name} missing on {table}"


async def _make_session_with_message(db_session):
    """Insert a minimal user→course→unit→disease→session→message graph."""
    from app.models.course import Course
    from app.models.unit import Unit
    from app.models.disease import Disease
    from app.models.session import Session as SessionModel
    from app.models.message import Message, MessageRole

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
        unit_id=unit.id, name="D", category="cat", key_symptoms=[],
        differentials=[], difficulty_tier=1, speech_style="s", nudge_behavior={},
    )
    db_session.add(disease)
    await db_session.flush()
    session = SessionModel(
        disease_id=disease.id, user_id=user.id, course_id=course.id,
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    await db_session.flush()
    msg = Message(
        session_id=session.id, role=MessageRole.student, content="hi",
        sent_at=datetime.now(timezone.utc),
    )
    db_session.add(msg)
    await db_session.commit()
    return session, msg


async def test_deleting_session_cascades_messages(clean_tables, db_session):
    from sqlalchemy import select
    from app.models.session import Session as SessionModel
    from app.models.message import Message

    session, msg = await _make_session_with_message(db_session)
    await db_session.delete(await db_session.get(SessionModel, session.id))
    await db_session.commit()
    remaining = (await db_session.execute(select(Message).where(Message.id == msg.id))).first()
    assert remaining is None, "message should be cascade-deleted with its session"


async def test_quiet_hours_must_be_paired(clean_tables, db_session):
    user = User(
        google_uid=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.com",
        quiet_hours_start=time(22, 0), quiet_hours_end=None,
    )
    db_session.add(user)
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_quiet_hours_both_set_ok(clean_tables, db_session):
    user = User(
        google_uid=str(uuid.uuid4()), email=f"{uuid.uuid4()}@x.com",
        quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0),
    )
    db_session.add(user)
    await db_session.commit()  # should not raise
