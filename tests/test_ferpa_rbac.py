import uuid
from datetime import datetime, timezone

import pytest

from app.models.course import Course
from app.models.message import Message, MessageRole
from app.services.grading_service import _build_transcript


async def _make_course(db_session, professor_user):
    course = Course(
        id=uuid.uuid4(),
        title="Course",
        professor_id=professor_user.id,
        class_code=uuid.uuid4().hex[:6].upper().replace("0", "A"),
        semester="Fall 2026",
        is_active=True,
    )
    db_session.add(course)
    await db_session.commit()
    return course


@pytest.mark.asyncio
async def test_professor_cannot_read_other_professors_course(clean_tables, client, db_session, professor, rsa_keys):
    prof_a, _ = professor
    private_pem, _ = rsa_keys
    # A second professor
    from app.models.user import User, UserRole
    from tests.conftest import _make_token
    prof_b = User(
        id=uuid.uuid4(), google_uid=f"prof-{uuid.uuid4().hex}",
        email=f"b-{uuid.uuid4().hex[:8]}@test.edu", role=UserRole.professor,
        is_verified=False, display_name="B",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(prof_b)
    await db_session.commit()
    token_b = _make_token(prof_b.id, private_pem)

    course = await _make_course(db_session, prof_a)
    resp = await client.get(f"/api/v1/courses/{course.id}", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_student_cannot_read_other_students_session(clean_tables, client, db_session, student, rsa_keys):
    from app.models.disease import Disease
    from app.models.unit import Unit, UnitStatus
    from app.models.session import Session, SessionStatus
    from app.models.user import User, UserRole
    from tests.conftest import _make_token

    stu_a, _ = student
    private_pem, _ = rsa_keys
    stu_b = User(
        id=uuid.uuid4(), google_uid=f"stu-{uuid.uuid4().hex}",
        email=f"b-{uuid.uuid4().hex[:8]}@test.edu", role=UserRole.student,
        is_verified=True, display_name="B",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(stu_b)
    await db_session.commit()
    token_b = _make_token(stu_b.id, private_pem)

    # A professor + course + unit + disease + a session owned by student A
    prof = User(
        id=uuid.uuid4(), google_uid=f"prof-{uuid.uuid4().hex}",
        email=f"p-{uuid.uuid4().hex[:8]}@test.edu", role=UserRole.professor,
        is_verified=False, display_name="P",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    db_session.add(prof)
    await db_session.commit()
    course = await _make_course(db_session, prof)
    unit = Unit(id=uuid.uuid4(), course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.commit()
    disease = Disease(
        id=uuid.uuid4(), unit_id=unit.id, name="MDD", category="mood", difficulty_tier=1,
        key_symptoms=["low mood", "anhedonia"], differentials=["bipolar disorder"],
        speech_style="flat", nudge_behavior={}, is_active=True,
    )
    db_session.add(disease)
    await db_session.commit()
    session = Session(
        id=uuid.uuid4(), user_id=stu_a.id, course_id=course.id, disease_id=disease.id,
        status=SessionStatus.active, turn_count=0, started_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    await db_session.commit()

    resp = await client.get(f"/api/v1/sessions/{session.id}", headers={"Authorization": f"Bearer {token_b}"})
    assert resp.status_code == 404


def test_transcript_contains_no_student_pii():
    messages = [
        Message(id=uuid.uuid4(), session_id=uuid.uuid4(), role=MessageRole.student,
                content="Hello, I am worried.", sent_at=datetime.now(timezone.utc)),
        Message(id=uuid.uuid4(), session_id=uuid.uuid4(), role=MessageRole.patient,
                content="I feel low.", sent_at=datetime.now(timezone.utc)),
    ]
    transcript = _build_transcript(messages)
    # Only generic speaker labels — no names, emails, or user ids.
    assert "Student:" in transcript
    assert "Patient:" in transcript
    assert "@" not in transcript
