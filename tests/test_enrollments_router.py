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
