import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")


async def test_student_joins_valid_course(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101", "semester": "Fall 2026"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert create_resp.status_code == 201
    class_code = create_resp.json()["class_code"]

    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Psych 101"
    assert body["student_count"] == 1


async def test_student_joins_invalid_code_returns_404(client, student):
    _, stu_token = student
    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": "XXXXXX"},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 404


async def test_student_joins_already_enrolled_returns_409(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    class_code = create_resp.json()["class_code"]

    await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 409


async def test_student_joins_inactive_course_returns_410(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Closed Course"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]
    class_code = create_resp.json()["class_code"]

    await client.delete(
        f"/api/v1/courses/{course_id}/deactivate",
        headers={"Authorization": f"Bearer {prof_token}"},
    )

    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert response.status_code == 410


async def test_professor_cannot_join_course(client, professor):
    _, prof_token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "My Course"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    class_code = create_resp.json()["class_code"]

    response = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert response.status_code == 403


async def test_professor_lists_enrolled_students(client, professor, student):
    prof_user, prof_token = professor
    stu_user, stu_token = student

    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]
    class_code = create_resp.json()["class_code"]

    await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )

    response = await client.get(
        f"/api/v1/courses/{course_id}/students",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert response.status_code == 200
    students = response.json()
    assert len(students) == 1
    assert students[0]["user_id"] == str(stu_user.id)
    assert "enrolled_at" in students[0]


async def test_list_students_wrong_professor_returns_404(client, professor, rsa_keys):
    _, prof_token = professor
    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "My Course"},
        headers={"Authorization": f"Bearer {prof_token}"},
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
        google_uid=f"other3-{uuid.uuid4().hex}",
        email=f"other3-{uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=True,
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

    response = await client.get(
        f"/api/v1/courses/{course_id}/students",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404


async def test_professor_removes_student(client, professor, student):
    prof_user, prof_token = professor
    stu_user, stu_token = student

    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Psych 101"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    course_id = create_resp.json()["id"]
    class_code = create_resp.json()["class_code"]

    await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )

    response = await client.delete(
        f"/api/v1/courses/{course_id}/students/{stu_user.id}",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert response.status_code == 204

    students_resp = await client.get(
        f"/api/v1/courses/{course_id}/students",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert students_resp.json() == []


async def test_student_count_updates_after_enrollment(client, professor, student):
    _, prof_token = professor
    _, stu_token = student

    create_resp = await client.post(
        "/api/v1/courses",
        json={"title": "Count Test"},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert create_resp.json()["student_count"] == 0
    course_id = create_resp.json()["id"]
    class_code = create_resp.json()["class_code"]

    join_resp = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": class_code},
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert join_resp.json()["student_count"] == 1

    detail_resp = await client.get(
        f"/api/v1/courses/{course_id}",
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert detail_resp.json()["student_count"] == 1


async def test_join_course_rejects_invalid_class_code(client, clean_tables, student):
    user, token = student
    resp = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": "TOOLONG7"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": "AB"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_join_course_rejects_null_class_code(client, clean_tables, student):
    user, token = student
    resp = await client.post(
        "/api/v1/enrollments/join",
        json={"class_code": None},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
