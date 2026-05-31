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
    # With the standardized error handler, dict-detail is spread to the top level
    # (no "detail" wrapper); check "errors" or "message" directly on the body.
    assert isinstance(body.get("errors"), list) and len(body["errors"]) > 0
    assert body.get("code") == "BAD_REQUEST"


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


async def test_upload_dotfile_filename_returns_400(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    files = {"file": (".json", b'{"units":[]}', "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def _sample_v2_bytes() -> bytes:
    return (Path(__file__).parent / "fixtures" / "sample_diseases_v2.json").read_bytes()


async def test_first_upload_diff_is_none(client, professor):
    _, token = professor
    course = await _create_course(client, token)

    files = {"file": ("sample.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["diff"] is None


async def test_reupload_preview_includes_diff(client, professor):
    _, token = professor
    course = await _create_course(client, token)

    # First upload + confirm to create DB state
    files = {"file": ("sample.json", _sample_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Second upload (v2) — should return diff
    files2 = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files2,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    diff = resp.json()["diff"]
    assert diff is not None
    assert diff["diseases_added"] == 2
    assert diff["diseases_modified"] == 1
    assert diff["diseases_removed"] == 4
    assert "Unit 3: Psychotic Disorders" in diff["units_added"]
    assert "Unit 2: Anxiety Disorders" in diff["units_orphaned"]


# ---------------------------------------------------------------------------
# Diff-apply confirm tests (Task 8)
# ---------------------------------------------------------------------------


async def _upload_and_confirm(client, token, course_id, file_bytes):
    files = {"file": ("sample.json", file_bytes, "application/json")}
    upload = await client.post(
        f"/api/v1/courses/{course_id}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert upload.status_code == 200
    confirm = await client.post(
        f"/api/v1/courses/{course_id}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert confirm.status_code == 200
    return confirm.json()


async def test_confirm_first_upload_diff_has_only_added(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    result = await _upload_and_confirm(client, token, course["id"], _sample_bytes())
    diff = result["diff"]
    assert diff["diseases_added"] == 6
    assert diff["diseases_modified"] == 0
    assert diff["diseases_removed"] == 0
    assert len(diff["units_added"]) == 2
    assert diff["units_orphaned"] == []


async def test_confirm_with_released_unit_succeeds(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    # Release a unit directly in DB
    async with _fresh_session() as s:
        from app.models.unit import Unit, UnitStatus
        from datetime import datetime, timezone
        from sqlalchemy import select
        units = (await s.execute(select(Unit))).scalars().all()
        units[0].status = UnitStatus.released
        units[0].release_date = datetime.now(timezone.utc)
        await s.commit()

    # Re-upload + confirm should succeed (no 409)
    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    confirm = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert confirm.status_code == 200


async def test_confirm_reupload_soft_deletes_removed_diseases(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    result = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 200
    diff = result.json()["diff"]
    assert diff["diseases_removed"] == 4

    # Verify total active disease count via units endpoint:
    # Unit 1: MDD (modified), Bipolar I (unchanged), Cyclothymia (new) = 3
    # Unit 3: Schizophrenia (new) = 1
    # Unit 2: (orphaned, all diseases soft-deleted) = 0
    # Total active = 4
    units_resp = await client.get(
        f"/api/v1/courses/{course['id']}/units",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert units_resp.status_code == 200
    total_active = sum(u["disease_count"] for u in units_resp.json())
    assert total_active == 4


async def test_confirm_reupload_updates_modified_disease_in_place(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    # Record MDD's disease ID before re-upload
    async with _fresh_session() as s:
        from app.models.disease import Disease
        from sqlalchemy import select
        mdd = (await s.execute(select(Disease).where(Disease.name == "Major Depressive Disorder"))).scalar_one()
        mdd_id_before = mdd.id
        assert mdd.difficulty_tier == 2

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )

    async with _fresh_session() as s:
        from app.models.disease import Disease
        from sqlalchemy import select
        mdd = (await s.execute(select(Disease).where(Disease.name == "Major Depressive Disorder"))).scalar_one()
        # Same DB row (same id), updated field
        assert mdd.id == mdd_id_before
        assert mdd.difficulty_tier == 3


async def test_confirm_reupload_creates_new_diseases(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    result = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 200
    diff = result.json()["diff"]
    assert diff["diseases_added"] == 2  # Cyclothymia + Schizophrenia


async def test_confirm_reupload_creates_new_unit(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    result = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert result.status_code == 200
    diff = result.json()["diff"]
    assert "Unit 3: Psychotic Disorders" in diff["units_added"]

    units_resp = await client.get(
        f"/api/v1/courses/{course['id']}/units",
        headers={"Authorization": f"Bearer {token}"},
    )
    labels = [u["label"] for u in units_resp.json()]
    assert "Unit 3: Psychotic Disorders" in labels


async def test_confirm_orphaned_unit_leaves_unit_row_intact(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_and_confirm(client, token, course["id"], _sample_bytes())

    files = {"file": ("sample.json", _sample_v2_bytes(), "application/json")}
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Unit 2 row still exists (professor can see it)
    units_resp = await client.get(
        f"/api/v1/courses/{course['id']}/units",
        headers={"Authorization": f"Bearer {token}"},
    )
    labels = [u["label"] for u in units_resp.json()]
    assert "Unit 2: Anxiety Disorders" in labels
    orphaned_unit = next(u for u in units_resp.json() if u["label"] == "Unit 2: Anxiety Disorders")
    # All its diseases were soft-deleted
    assert orphaned_unit["disease_count"] == 0
