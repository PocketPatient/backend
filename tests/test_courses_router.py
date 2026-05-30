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


async def test_professor_updates_course(client, professor):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Old Title", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]
    response = await client.put(
        f"/api/v1/courses/{course_id}",
        json={"title": "New Title", "semester": "Spring 2027"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "New Title"
    assert body["semester"] == "Spring 2027"


async def test_update_course_wrong_owner_returns_404(client, professor, rsa_keys):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Mine"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]

    import uuid
    from datetime import datetime, timezone
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models.user import User, UserRole
    from tests.conftest import TEST_DATABASE_URL, _make_token
    private_pem, _ = rsa_keys
    other = User(
        id=uuid.uuid4(),
        google_uid=f"other2-{uuid.uuid4().hex}",
        email=f"other2-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Other Prof",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        s.add(other)
        await s.commit()
    await engine.dispose()
    other_token = _make_token(other.id, private_pem)

    response = await client.put(
        f"/api/v1/courses/{course_id}",
        json={"title": "Hijacked"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404


async def test_professor_deactivates_course(client, professor):
    _, token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Active Course"},
        headers={"Authorization": f"Bearer {token}"},
    )
    course_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/v1/courses/{course_id}/deactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False


async def test_student_cannot_deactivate_course(client, student, professor):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Prof Course"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]
    response = await client.delete(
        f"/api/v1/courses/{course_id}/deactivate",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 403


async def test_update_course_window_start_after_end_returns_422(client, professor, clean_tables):
    _, token = professor
    create = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    course_id = create.json()["id"]

    resp = await client.put(
        f"/api/v1/courses/{course_id}",
        json={"msg_window_start": "22:00:00", "msg_window_end": "08:00:00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_update_course_invalid_timezone_returns_422(client, professor, clean_tables):
    _, token = professor
    create = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    course_id = create.json()["id"]

    resp = await client.put(
        f"/api/v1/courses/{course_id}",
        json={"msg_timezone": "Fake/NotReal"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_update_course_valid_messaging_settings(client, professor, clean_tables):
    _, token = professor
    create = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    course_id = create.json()["id"]

    resp = await client.put(
        f"/api/v1/courses/{course_id}",
        json={
            "msg_window_start": "09:00:00",
            "msg_window_end": "21:00:00",
            "msg_timezone": "America/Chicago",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["msg_window_start"] == "09:00:00"
    assert data["msg_window_end"] == "21:00:00"
    assert data["msg_timezone"] == "America/Chicago"
