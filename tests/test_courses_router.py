import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")


async def test_professor_creates_course(client, professor):
    _, token = professor
    response = await client.post(
        "/api/v1/courses",
        json={"title": "Psychiatry 101", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Psychiatry 101"
    assert body["semester"] == "Fall 2026"
    assert len(body["class_code"]) == 6
    assert body["class_code"].isupper()
    assert all(c in "ABCDEFGHJKMNPQRSTUVWXYZ23456789" for c in body["class_code"])
    assert body["student_count"] == 0
    assert body["is_active"] is True


async def test_student_cannot_create_course(client, student):
    _, token = student
    response = await client.post(
        "/api/v1/courses",
        json={"title": "Psychiatry 101", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


async def test_create_course_no_auth(client):
    response = await client.post(
        "/api/v1/courses",
        json={"title": "Psychiatry 101"},
    )
    assert response.status_code == 401


async def test_professor_list_courses_sees_own(client, professor):
    _, token = professor
    await client.post(
        "/api/v1/courses",
        json={"title": "Course A", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/api/v1/courses",
        json={"title": "Course B", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = await client.get("/api/v1/courses", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    titles = [c["title"] for c in response.json()]
    assert "Course A" in titles
    assert "Course B" in titles


@pytest.mark.skip(reason="requires POST /enrollments/join — enabled in Task 6")
async def test_student_list_courses_sees_enrolled(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    class_code = create_resp.json()["class_code"]
    await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    response = await client.get("/api/v1/courses", headers={"Authorization": f"Bearer {stu_token}"})
    assert response.status_code == 200
    assert any(c["title"] == "Psych 101" for c in response.json())


async def test_get_course_detail_professor(client, professor):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Detail Test", "semester": "Spring 2027"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]
    response = await client.get(
        f"/api/v1/courses/{course_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Detail Test"


async def test_get_course_detail_wrong_professor_returns_404(client, professor, rsa_keys):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Secret Course"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]

    import uuid
    import os
    from datetime import datetime, timezone
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models.user import User, UserRole
    from tests.conftest import TEST_DATABASE_URL, _make_token
    private_pem, _ = rsa_keys
    other = User(
        id=uuid.uuid4(),
        google_uid=f"other-{uuid.uuid4().hex}",
        email=f"other-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Other Professor",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    _engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _session_factory() as s:
        s.add(other)
        await s.commit()
    await _engine.dispose()
    other_token = _make_token(other.id, private_pem)
    response = await client.get(
        f"/api/v1/courses/{course_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404
