"""Professor verification: admin endpoints + operator CLI."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app import admin_cli
from app.models.user import User, UserRole
from tests.conftest import _make_token

pytestmark = pytest.mark.usefixtures("clean_tables")


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def _user(db_session, rsa_keys, role, *, verified=None, email=None, deleted=False):
    user = User(
        google_uid=f"u-{uuid.uuid4().hex}",
        email=email or f"{uuid.uuid4().hex[:8]}@rutgers.edu",
        role=role,
        is_verified=verified,
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, _make_token(user.id, rsa_keys[0])


async def test_pending_list_and_verify_unlocks_course_creation(client, db_session, rsa_keys):
    _, admin_token = await _user(db_session, rsa_keys, UserRole.admin, verified=True)
    prof, prof_token = await _user(db_session, rsa_keys, UserRole.professor, verified=False)
    await _user(db_session, rsa_keys, UserRole.professor, verified=True)  # already verified
    await _user(db_session, rsa_keys, UserRole.professor, verified=False, deleted=True)
    await _user(db_session, rsa_keys, UserRole.student, verified=True)

    pending = await client.get("/api/v1/admin/professors/pending", headers=_auth(admin_token))
    assert pending.status_code == 200
    assert [u["id"] for u in pending.json()] == [str(prof.id)]

    blocked = await client.post("/api/v1/courses", json={"title": "Psych"}, headers=_auth(prof_token))
    assert blocked.status_code == 403

    resp = await client.post(f"/api/v1/admin/users/{prof.id}/verify-professor", headers=_auth(admin_token))
    assert resp.status_code == 200
    assert resp.json()["is_verified"] is True

    created = await client.post("/api/v1/courses", json={"title": "Psych"}, headers=_auth(prof_token))
    assert created.status_code == 201
    assert (await client.get("/api/v1/admin/professors/pending", headers=_auth(admin_token))).json() == []


async def test_verify_rejects_non_professors_and_deleted(client, db_session, rsa_keys):
    _, admin_token = await _user(db_session, rsa_keys, UserRole.admin, verified=True)
    student, _ = await _user(db_session, rsa_keys, UserRole.student, verified=True)
    gone, _ = await _user(db_session, rsa_keys, UserRole.professor, verified=False, deleted=True)
    for target in (student.id, gone.id, uuid.uuid4()):
        resp = await client.post(f"/api/v1/admin/users/{target}/verify-professor", headers=_auth(admin_token))
        assert resp.status_code == 404


async def test_professor_endpoints_are_admin_only(client, db_session, rsa_keys):
    prof, prof_token = await _user(db_session, rsa_keys, UserRole.professor, verified=True)
    _, stu_token = await _user(db_session, rsa_keys, UserRole.student, verified=True)
    for token in (prof_token, stu_token):
        assert (await client.get("/api/v1/admin/professors/pending", headers=_auth(token))).status_code == 403
        assert (await client.post(f"/api/v1/admin/users/{prof.id}/verify-professor",
                                  headers=_auth(token))).status_code == 403


async def test_cli_make_admin_and_verify_professor(db_session, rsa_keys):
    await _user(db_session, rsa_keys, None, email="Boss@Rutgers.edu")
    prof, _ = await _user(db_session, rsa_keys, UserRole.professor, verified=False, email="prof@rutgers.edu")

    assert (await admin_cli.make_admin(db_session, "boss@rutgers.edu")).startswith("ok")
    assert "prof@rutgers.edu" in await admin_cli.list_pending_professors(db_session)
    assert (await admin_cli.verify_professor(db_session, "PROF@rutgers.edu")).startswith("ok")
    assert await admin_cli.list_pending_professors(db_session) == "(none pending)"

    assert (await admin_cli.make_admin(db_session, "nobody@rutgers.edu")).startswith("error")
    assert (await admin_cli.verify_professor(db_session, "boss@rutgers.edu")).startswith("error")

    boss = (await db_session.execute(
        User.__table__.select().where(User.email == "Boss@Rutgers.edu"))).first()
    assert boss.role == UserRole.admin
