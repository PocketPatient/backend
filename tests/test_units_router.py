from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from jose import jwt

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from tests.conftest import _make_token
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "rarely", "tone": "flat", "example": ""}


@pytest_asyncio.fixture
async def setup(professor, student, db_session):
    prof, prof_token = professor
    stu, stu_token = student

    course = Course(
        title="Psych 101",
        professor_id=prof.id,
        class_code="TSTCRS",
        is_active=True,
    )
    db_session.add(course)
    await db_session.flush()

    enrollment = Enrollment(user_id=stu.id, course_id=course.id)
    db_session.add(enrollment)

    unit_draft = Unit(course_id=course.id, label="Unit 1: Mood", status=UnitStatus.draft)
    unit_released = Unit(
        course_id=course.id,
        label="Unit 2: Anxiety",
        status=UnitStatus.released,
        release_date=datetime.now(timezone.utc),
    )
    unit_closed = Unit(course_id=course.id, label="Unit 3: Psychosis", status=UnitStatus.closed)
    db_session.add_all([unit_draft, unit_released, unit_closed])
    await db_session.flush()

    active_disease = Disease(
        unit_id=unit_released.id,
        name="GAD",
        category="Anxiety",
        key_symptoms=["worry"],
        differentials=["MDD"],
        difficulty_tier=2,
        speech_style="anxious",
        nudge_behavior=_NUDGE,
        is_active=True,
    )
    inactive_disease = Disease(
        unit_id=unit_released.id,
        name="Old Disease",
        category="Anxiety",
        key_symptoms=["x"],
        differentials=["y"],
        difficulty_tier=1,
        speech_style="flat",
        nudge_behavior=_NUDGE,
        is_active=False,
    )
    db_session.add_all([active_disease, inactive_disease])
    await db_session.commit()
    await db_session.refresh(unit_draft)
    await db_session.refresh(unit_released)
    await db_session.refresh(unit_closed)

    return course, unit_draft, unit_released, unit_closed, active_disease, prof_token, stu_token


async def test_list_units_professor_sees_all_statuses(client, setup):
    course, _, _, _, _, prof_token, _ = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    statuses = {u["status"] for u in resp.json()}
    assert statuses == {"draft", "released", "closed"}


async def test_list_units_professor_sees_disease_details(client, setup):
    course, _, _, _, active_disease, prof_token, _ = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    released = next(u for u in resp.json() if u["status"] == "released")
    assert released["disease_count"] == 1
    assert len(released["diseases"]) == 1
    assert released["diseases"][0]["name"] == "GAD"


async def test_list_units_student_sees_released_only(client, setup):
    course, _, _, _, _, _, stu_token = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "released"


async def test_list_units_student_has_no_diseases_field(client, setup):
    course, _, _, _, _, _, stu_token = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 200
    unit = resp.json()[0]
    assert "diseases" not in unit
    assert unit["disease_count"] == 1


async def test_list_units_professor_not_owner_returns_404(client, setup, db_session, rsa_keys):
    course, *_, prof_token, _ = setup
    private_pem, _ = rsa_keys
    other_prof = User(
        google_uid=f"p2-{uuid.uuid4().hex}",
        email=f"p2-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=True,
        display_name="Other",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(other_prof)
    await db_session.commit()
    other_token = _make_token(other_prof.id, private_pem)

    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


async def test_list_units_student_not_enrolled_returns_404(client, setup, db_session, rsa_keys):
    course, *_ = setup
    private_pem, _ = rsa_keys
    other_stu = User(
        google_uid=f"s2-{uuid.uuid4().hex}",
        email=f"s2-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.student,
        is_verified=True,
        display_name="Other",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(other_stu)
    await db_session.commit()
    other_token = _make_token(other_stu.id, private_pem)

    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


async def test_release_draft_unit(client, setup):
    course, unit_draft, _, _, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_draft.id}/release",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "released"
    assert data["release_date"] is not None


async def test_release_already_released_returns_409(client, setup):
    course, _, unit_released, _, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_released.id}/release",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 409


async def test_release_closed_unit_returns_409(client, setup):
    course, _, _, unit_closed, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_closed.id}/release",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 409


async def test_close_released_unit(client, setup):
    course, _, unit_released, _, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_released.id}/close",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


async def test_close_draft_unit_returns_409(client, setup):
    course, unit_draft, _, _, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_draft.id}/close",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 409


async def test_close_already_closed_returns_409(client, setup):
    course, _, _, unit_closed, _, prof_token, _ = setup
    resp = await client.put(
        f"/api/v1/courses/{course.id}/units/{unit_closed.id}/close",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 409


async def test_disease_pool_returns_active_released_only(client, setup):
    course, _, _, _, active_disease, prof_token, _ = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/disease-pool",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "GAD"


async def test_disease_pool_student_forbidden(client, setup):
    course, *_, stu_token = setup
    resp = await client.get(
        f"/api/v1/courses/{course.id}/disease-pool",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 403


async def test_list_units_professor_multiple_units_no_n1(client, clean_tables, professor, db_session):
    """Regression: list_units must return correct data for multiple units."""
    user, token = professor
    course = Course(title="Multi-Unit Course", professor_id=user.id, class_code="MUC234")
    db_session.add(course)
    await db_session.flush()

    unit_a = Unit(course_id=course.id, label="Unit A", status=UnitStatus.draft)
    unit_b = Unit(course_id=course.id, label="Unit B", status=UnitStatus.draft)
    db_session.add_all([unit_a, unit_b])
    await db_session.flush()

    db_session.add(Disease(
        unit_id=unit_a.id, name="Disease A1", category="Mood", key_symptoms=["s1"],
        differentials=["d1"], difficulty_tier=1, speech_style="flat",
        nudge_behavior={"frequency": "low", "tone": "neutral", "example": ""},
    ))
    db_session.add(Disease(
        unit_id=unit_b.id, name="Disease B1", category="Anxiety", key_symptoms=["s1"],
        differentials=["d1"], difficulty_tier=2, speech_style="rapid",
        nudge_behavior={"frequency": "high", "tone": "urgent", "example": ""},
    ))
    await db_session.commit()

    resp = await client.get(
        f"/api/v1/courses/{course.id}/units",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    labels = {u["label"] for u in data}
    assert labels == {"Unit A", "Unit B"}
    for unit_data in data:
        assert unit_data["disease_count"] == 1
