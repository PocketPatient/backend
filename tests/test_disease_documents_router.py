import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")

SAMPLE_DOC_PATH = Path(__file__).parent / "fixtures" / "sample_diseases.json"


def _sample_bytes() -> bytes:
    return SAMPLE_DOC_PATH.read_bytes()


async def _create_course(client, prof_token, title="Psych 101"):
    resp = await client.post(
        "/api/v1/courses",
        json={"title": title},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 201
    return resp.json()


async def test_upload_disease_document_happy_path(client, professor):
    _, token = professor
    course = await _create_course(client, token)

    files = {"file": ("sample.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 1
    assert body["errors"] == []
    assert len(body["units"]) == 2
    labels = [u["label"] for u in body["units"]]
    assert "Unit 1: Mood Disorders" in labels
    mood = next(u for u in body["units"] if u["label"] == "Unit 1: Mood Disorders")
    assert mood["disease_count"] == 3
    assert "Major Depressive Disorder" in mood["diseases"]


async def test_upload_unsupported_extension_returns_400(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    files = {"file": ("data.txt", b"hello", "text/plain")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


async def test_upload_by_student_returns_403(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    course = await _create_course(client, prof_token)

    files = {"file": ("doc.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 403


async def test_upload_to_nonexistent_course_returns_404(client, professor):
    _, token = professor
    import uuid as _uuid
    fake = _uuid.uuid4()
    files = {"file": ("doc.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{fake}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_upload_by_non_owner_professor_returns_404(client, professor, rsa_keys):
    _, owner_token = professor
    course = await _create_course(client, owner_token)

    import uuid as _uuid
    from datetime import datetime, timezone
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models.user import User, UserRole
    from tests.conftest import TEST_DATABASE_URL, _make_token
    private_pem, _ = rsa_keys
    other = User(
        id=_uuid.uuid4(),
        google_uid=f"otherprof-{_uuid.uuid4().hex}",
        email=f"otherprof-{_uuid.uuid4().hex[:8]}@test.edu",
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

    files = {"file": ("doc.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


async def test_upload_with_parse_errors_returns_200_with_errors(client, professor):
    _, token = professor
    course = await _create_course(client, token)

    bad = {
        "units": [
            {
                "label": "U1",
                "diseases": [
                    {
                        "name": "Good",
                        "category": "C",
                        "key_symptoms": ["s"],
                        "differentials": ["d"],
                        "difficulty_tier": 1,
                        "speech_style": "flat",
                        "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                    },
                    {
                        "name": "",
                        "category": "C",
                        "key_symptoms": ["s"],
                        "differentials": ["d"],
                        "difficulty_tier": 1,
                        "speech_style": "flat",
                        "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                    },
                ],
            }
        ]
    }
    files = {"file": ("bad.json", json.dumps(bad).encode("utf-8"), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["errors"]) >= 1
    assert body["units"][0]["disease_count"] == 1


async def test_upload_second_time_increments_version(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    files = {"file": ("doc.json", _sample_bytes(), "application/json")}

    r1 = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.json()["version"] == 1

    r2 = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.json()["version"] == 2
