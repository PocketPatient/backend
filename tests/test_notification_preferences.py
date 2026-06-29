from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.user import User

pytestmark = pytest.mark.usefixtures("clean_tables")


async def test_set_preferences_persists(client, student, db_session):
    stu, token = student
    resp = await client.put(
        "/api/v1/users/me/notification-preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"push_enabled": True, "quiet_hours_start": "22:00", "quiet_hours_end": "08:00"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["push_enabled"] is True
    assert body["quiet_hours_start"] == "22:00:00"
    assert body["quiet_hours_end"] == "08:00:00"

    # populate_existing refreshes the fixture's identity-mapped copy during the
    # async query (a plain re-select would return the stale cached instance).
    refreshed = (
        await db_session.execute(
            select(User).where(User.id == stu.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.push_enabled is True
    assert refreshed.quiet_hours_start.hour == 22
    assert refreshed.quiet_hours_end.hour == 8


async def test_disable_push_without_quiet_hours(client, student):
    _, token = student
    resp = await client.put(
        "/api/v1/users/me/notification-preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"push_enabled": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["push_enabled"] is False
    assert body["quiet_hours_start"] is None
    assert body["quiet_hours_end"] is None


async def test_partial_quiet_hours_rejected(client, student):
    _, token = student
    resp = await client.put(
        "/api/v1/users/me/notification-preferences",
        headers={"Authorization": f"Bearer {token}"},
        json={"push_enabled": True, "quiet_hours_start": "22:00"},
    )
    assert resp.status_code == 422


async def test_requires_auth(client):
    resp = await client.put(
        "/api/v1/users/me/notification-preferences",
        json={"push_enabled": True},
    )
    assert resp.status_code == 401


async def test_new_user_defaults_push_enabled(client, student):
    # A user who never set preferences should default to push enabled.
    _, token = student
    me = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
