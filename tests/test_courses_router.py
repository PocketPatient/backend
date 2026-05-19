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
