import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")


@asynccontextmanager
async def _fresh_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as session:
        yield session
    await engine.dispose()

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


# ---------------------------------------------------------------------------
# Confirm endpoint tests
# ---------------------------------------------------------------------------


async def _upload_sample(client, course_id, token, payload: bytes | None = None):
    data = payload if payload is not None else _sample_bytes()
    files = {"file": ("doc.json", data, "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course_id}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_confirm_happy_path(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_sample(client, course["id"], token)

    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["units_created"] == 2
    assert body["diseases_created"] == 6
    assert body["version"] == 1


async def test_confirm_with_no_pending_upload_returns_404(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_confirm_with_parse_errors_returns_400(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    bad = {
        "units": [
            {
                "label": "U1",
                "diseases": [
                    {
                        "name": "",
                        "category": "C",
                        "key_symptoms": ["s"],
                        "differentials": ["d"],
                        "difficulty_tier": 1,
                        "speech_style": "flat",
                        "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                    }
                ],
            }
        ]
    }
    await _upload_sample(client, course["id"], token, payload=json.dumps(bad).encode("utf-8"))

    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "errors" in body["detail"] or "parse" in str(body["detail"]).lower()


async def test_confirm_when_file_missing_returns_410(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    preview = await _upload_sample(client, course["id"], token)

    # Manually delete the uploaded file from disk to simulate expiry.
    import uuid as _uuid
    from pathlib import Path
    from sqlalchemy import select
    from app.models.disease_document import DiseaseDocument

    doc_id = _uuid.UUID(preview["document_id"])
    async with _fresh_session() as s:
        row = (await s.execute(select(DiseaseDocument).where(DiseaseDocument.id == doc_id))).scalar_one()
        Path(row.file_url).unlink()

    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 410


async def test_confirm_replaces_existing_units(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_sample(client, course["id"], token)
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Upload a smaller payload and confirm again
    smaller = {
        "units": [
            {
                "label": "Just one",
                "diseases": [
                    {
                        "name": "Solo",
                        "category": "Cat",
                        "key_symptoms": ["a"],
                        "differentials": ["b"],
                        "difficulty_tier": 1,
                        "speech_style": "flat",
                        "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                    }
                ],
            }
        ]
    }
    await _upload_sample(client, course["id"], token, payload=json.dumps(smaller).encode("utf-8"))
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["units_created"] == 1
    assert body["diseases_created"] == 1

    # Verify only the new unit/disease remain
    import uuid as _uuid
    from sqlalchemy import select, func
    from app.models.unit import Unit
    from app.models.disease import Disease
    async with _fresh_session() as s:
        unit_count = (await s.execute(select(func.count()).select_from(Unit).where(Unit.course_id == _uuid.UUID(course["id"])))).scalar_one()
        disease_count = (await s.execute(select(func.count()).select_from(Disease))).scalar_one()
    assert unit_count == 1
    assert disease_count == 1


async def test_confirm_blocked_by_released_unit_returns_409(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_sample(client, course["id"], token)
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Mark one unit as released directly in DB
    import uuid as _uuid
    from sqlalchemy import select
    from app.models.unit import Unit, UnitStatus
    async with _fresh_session() as s:
        unit = (await s.execute(select(Unit).where(Unit.course_id == _uuid.UUID(course["id"])))).scalars().first()
        unit.status = UnitStatus.released
        await s.commit()

    await _upload_sample(client, course["id"], token)
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


async def test_confirm_by_student_returns_403(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    course = await _create_course(client, prof_token)
    await _upload_sample(client, course["id"], prof_token)

    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 403
