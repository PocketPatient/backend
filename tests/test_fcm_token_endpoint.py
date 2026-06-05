from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.usefixtures("clean_tables")


async def test_put_fcm_token_sets_token(client, student, db_session):
    from sqlalchemy import select
    from app.models.user import User

    stu, token = student
    resp = await client.put(
        "/api/v1/users/me/fcm-token",
        json={"fcm_token": "device-token-abc123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    await db_session.refresh(stu)
    assert stu.fcm_token == "device-token-abc123"


async def test_put_fcm_token_overwrites_existing(client, student, db_session):
    stu, token = student

    await client.put(
        "/api/v1/users/me/fcm-token",
        json={"fcm_token": "old-token"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.put(
        "/api/v1/users/me/fcm-token",
        json={"fcm_token": "new-token"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    await db_session.refresh(stu)
    assert stu.fcm_token == "new-token"


async def test_put_fcm_token_empty_string_returns_422(client, student):
    _, token = student
    resp = await client.put(
        "/api/v1/users/me/fcm-token",
        json={"fcm_token": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_put_fcm_token_requires_auth(client):
    resp = await client.put(
        "/api/v1/users/me/fcm-token",
        json={"fcm_token": "token"},
    )
    assert resp.status_code == 401
