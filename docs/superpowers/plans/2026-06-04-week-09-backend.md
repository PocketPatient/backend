# Week 9 Backend Implementation Plan — Celery Scheduler + FCM Push Notifications

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up Celery + Redis for async case initiation, add FCM push notifications (new case + patient reply), and register a nudge task stub.

**Architecture:** Six tasks in dependency order — Task 1 (infra: deps, firebase.py, celery_app, docker-compose) and Task 2 (fcm_token column + endpoint) are independent and can run in parallel. Task 3 (push service + send_push task) depends on both. Tasks 4 (case initiation) and 5 (session_service push dispatch) both depend on Task 3 and can run in parallel. Task 6 verifies the full suite.

**Tech Stack:** Celery 5.3, Redis (sync client for tasks), firebase-admin 6.5, SQLAlchemy 2.0 async (tasks bridge via `asyncio.run()`), zoneinfo (stdlib), pytest + pytest-asyncio (asyncio_mode=auto), httpx AsyncClient.

**Spec:** `docs/superpowers/specs/2026-06-04-week09-celery-fcm-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `pyproject.toml` | Add `celery>=5.3,<6` |
| Create | `app/services/firebase.py` | `init_firebase()` — shared init for both FastAPI and Celery worker |
| Modify | `app/main.py` | Replace inline Firebase block with `init_firebase()` call |
| Create | `app/celery_app.py` | Celery instance + beat schedule + `worker_process_init` signal |
| Modify | `docker-compose.yml` | Add `celery-worker` and `celery-beat` services |
| Modify | `.env.example` | Add `FIREBASE_CREDENTIALS_PATH` line |
| Modify | `app/models/user.py` | Add `fcm_token: Mapped[str \| None]` column |
| Create | `alembic/versions/*_add_fcm_token_to_users.py` | Migration |
| Modify | `app/routers/users.py` | `PUT /users/me/fcm-token` endpoint |
| Create | `app/services/push_service.py` | `send_push_notification(token, title, body, data)` |
| Create | `app/tasks/__init__.py` | Empty package marker |
| Create | `app/tasks/push_notifications.py` | `send_push` Celery task + `_get_fcm_token` async helper |
| Create | `app/tasks/nudge.py` | `check_and_send_nudges` stub |
| Create | `app/tasks/case_initiation.py` | `check_and_initiate_cases` (beat) + `initiate_case` (delayed) |
| Modify | `app/services/session_service.py` | Dispatch `send_push` after bot reply |
| Create | `tests/test_push_service.py` | Unit tests for `push_service` |
| Create | `tests/test_push_task.py` | Tests for `_get_fcm_token` + `send_push` task |
| Create | `tests/test_fcm_token_endpoint.py` | Integration tests for `PUT /users/me/fcm-token` |
| Create | `tests/test_case_initiation.py` | Tests for scheduler tasks |
| Modify | `tests/test_sessions_router.py` | Assert `send_push` dispatched on bot reply |

---

## Task 1: Infrastructure — dependencies, firebase.py, celery_app, docker-compose

**Files:**
- Modify: `pyproject.toml`
- Create: `app/services/firebase.py`
- Modify: `app/main.py`
- Create: `app/celery_app.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

- [ ] **Step 1: Add celery to dependencies**

In `pyproject.toml`, add `"celery>=5.3,<6"` to the `dependencies` list (after the `redis` line):

```toml
dependencies = [
    "fastapi==0.111.0",
    "uvicorn[standard]==0.29.0",
    "sqlalchemy[asyncio]==2.0.49",
    "asyncpg==0.31.0",
    "alembic==1.18.4",
    "pydantic-settings==2.2.1",
    "python-jose[cryptography]==3.3.0",
    "httpx>=0.28.1",
    "firebase-admin==6.5.0",
    "redis==5.0.4",
    "celery>=5.3,<6",
    "google-genai>=1.16.1",
]
```

Run:
```bash
uv lock && uv sync
```
Expected: lock file updated, `celery` and its deps installed.

- [ ] **Step 2: Create app/services/firebase.py**

Create `app/services/firebase.py`:

```python
from __future__ import annotations

import firebase_admin
import firebase_admin.credentials

from app.config import settings


def init_firebase() -> None:
    if firebase_admin._apps:
        return
    if settings.firebase_credentials_path:
        cred = firebase_admin.credentials.Certificate(settings.firebase_credentials_path)
        firebase_admin.initialize_app(cred)
    elif settings.firebase_project_id:
        firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
```

- [ ] **Step 3: Update app/main.py to use init_firebase()**

In `app/main.py`, replace the inline Firebase init block in the `lifespan` function with a call to `init_firebase()`. The current block is:

```python
    if not firebase_admin._apps:
        if settings.firebase_credentials_path:
            cred = firebase_admin.credentials.Certificate(settings.firebase_credentials_path)
            firebase_admin.initialize_app(cred)
        elif settings.firebase_project_id:
            firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
```

Replace it with:

```python
    from app.services.firebase import init_firebase
    init_firebase()
```

Also remove the now-unused `import firebase_admin` and `import firebase_admin.credentials` lines at the top of `app/main.py`, since `firebase.py` owns those imports. Keep the `import firebase_admin` if it's used anywhere else in `main.py` — check first; if not, remove it.

- [ ] **Step 4: Create app/celery_app.py**

Create `app/celery_app.py`:

```python
from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from app.config import settings

celery = Celery("pocket_patient", broker=settings.redis_url)
celery.conf.update(
    result_backend=settings.redis_url,
    timezone="UTC",
    beat_schedule={
        "check-for-new-cases": {
            "task": "app.tasks.case_initiation.check_and_initiate_cases",
            "schedule": 900.0,  # every 15 minutes
        },
        "check-for-nudges": {
            "task": "app.tasks.nudge.check_and_send_nudges",
            "schedule": 3600.0,  # every hour
        },
    },
)


@worker_process_init.connect
def _init_firebase(**_kwargs: object) -> None:
    from app.services.firebase import init_firebase
    init_firebase()
```

- [ ] **Step 5: Add celery services to docker-compose.yml**

Open `docker-compose.yml` and add two new services after the `redis` service, before the `volumes` key:

```yaml
  celery-worker:
    build: .
    command: celery -A app.celery_app worker --loglevel=info
    env_file: .env
    depends_on:
      - db
      - redis

  celery-beat:
    build: .
    command: celery -A app.celery_app beat --loglevel=info
    env_file: .env
    depends_on:
      - db
      - redis
```

- [ ] **Step 6: Update .env.example**

Add `FIREBASE_CREDENTIALS_PATH` below the existing `FIREBASE_PROJECT_ID` line:

```
# Firebase
FIREBASE_PROJECT_ID=your-firebase-project-id
FIREBASE_CREDENTIALS_PATH=serviceAccountKey.json  # path to service account JSON, relative to project root
```

- [ ] **Step 7: Verify import smoke test**

Run:
```bash
uv run python -c "from app.celery_app import celery; print('celery ok:', celery)"
```
Expected output: `celery ok: <Celery pocket_patient at 0x...>`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock app/services/firebase.py app/main.py app/celery_app.py docker-compose.yml .env.example
git commit -m "feat: add Celery + shared Firebase init"
```

---

## Task 2: fcm_token column, migration, and PUT endpoint

**Files:**
- Modify: `app/models/user.py`
- Create: `alembic/versions/*_add_fcm_token_to_users.py`
- Modify: `app/routers/users.py`
- Create: `tests/test_fcm_token_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fcm_token_endpoint.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_fcm_token_endpoint.py -v
```
Expected: FAIL — `404 Not Found` (route doesn't exist yet) or `AttributeError` (fcm_token not on User).

- [ ] **Step 3: Add fcm_token column to User model**

In `app/models/user.py`, add `fcm_token` after `updated_at`:

```python
from sqlalchemy import Boolean, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

# ... existing fields ...
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    fcm_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 4: Generate and apply the migration**

```bash
uv run alembic revision --autogenerate -m "add fcm_token to users"
```

Open the generated file in `alembic/versions/`. Verify it contains something like:
```python
op.add_column('users', sa.Column('fcm_token', sa.String(length=512), nullable=True))
```
If it does, apply:
```bash
uv run alembic upgrade head
```
Expected: migration applies cleanly.

- [ ] **Step 5: Add PUT /users/me/fcm-token endpoint**

In `app/routers/users.py`, add a `FcmTokenRequest` schema and the endpoint after `set_role`:

```python
from pydantic import Field


class FcmTokenRequest(BaseModel):
    fcm_token: str = Field(min_length=1, max_length=512)


@router.put("/me/fcm-token", response_model=UserOut)
async def register_fcm_token(
    body: FcmTokenRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    current_user.fcm_token = body.fcm_token
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user
```

The existing imports in `users.py` already include `APIRouter, Depends, HTTPException`, `BaseModel`, `AsyncSession`, `get_db`, `get_current_user`, `User`, `UserRole`, `UserOut`. Add `Field` to the `pydantic` import line.

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_fcm_token_endpoint.py -v
```
Expected: PASS (4 tests). The test DB creates tables from `Base.metadata` which now includes `fcm_token`.

- [ ] **Step 7: Commit**

```bash
git add app/models/user.py alembic/versions/ app/routers/users.py tests/test_fcm_token_endpoint.py
git commit -m "feat: add fcm_token column + PUT /users/me/fcm-token endpoint"
```

---

## Task 3: Push service + send_push Celery task

**Files:**
- Create: `app/services/push_service.py`
- Create: `app/tasks/__init__.py`
- Create: `app/tasks/push_notifications.py`
- Create: `tests/test_push_service.py`
- Create: `tests/test_push_task.py`

**Note:** This task depends on Task 1 (celery_app) and Task 2 (fcm_token on User).

- [ ] **Step 1: Write failing tests for push_service**

Create `tests/test_push_service.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_send_push_notification_builds_correct_message():
    with patch("app.services.push_service.messaging") as mock_messaging:
        mock_messaging.Message.return_value = MagicMock()
        mock_messaging.Notification.return_value = MagicMock()

        from app.services.push_service import send_push_notification

        send_push_notification(
            token="device-token-123",
            title="Hello",
            body="World",
            data={"type": "new_case", "session_id": "abc"},
        )

        mock_messaging.Notification.assert_called_once_with(title="Hello", body="World")
        call_kwargs = mock_messaging.Message.call_args.kwargs
        assert call_kwargs["token"] == "device-token-123"
        assert call_kwargs["data"] == {"type": "new_case", "session_id": "abc"}
        mock_messaging.send.assert_called_once()


def test_send_push_notification_propagates_firebase_error():
    from firebase_admin.exceptions import FirebaseError

    with patch("app.services.push_service.messaging") as mock_messaging:
        mock_messaging.send.side_effect = FirebaseError(500, "upstream error")

        from app.services.push_service import send_push_notification

        with pytest.raises(FirebaseError):
            send_push_notification("token", "t", "b", {})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_push_service.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.push_service'`.

- [ ] **Step 3: Create app/services/push_service.py**

Create `app/services/push_service.py`:

```python
from __future__ import annotations

from firebase_admin import messaging


def send_push_notification(
    token: str, title: str, body: str, data: dict[str, str]
) -> None:
    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body),
        data=data,
    )
    messaging.send(message)
```

- [ ] **Step 4: Run tests to verify push_service tests pass**

```bash
uv run pytest tests/test_push_service.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Write failing tests for send_push task**

Create `tests/test_push_task.py`:

```python
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")


# --- _get_fcm_token async helper ---

async def test_get_fcm_token_returns_token_when_set(student, db_session):
    from app.tasks.push_notifications import _get_fcm_token

    stu, _ = student
    stu.fcm_token = "real-device-token"
    db_session.add(stu)
    await db_session.commit()

    # Patch AsyncSessionLocal inside the task module to use the test session.
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.push_notifications.AsyncSessionLocal", return_value=ctx):
        token = await _get_fcm_token(str(stu.id))

    assert token == "real-device-token"


async def test_get_fcm_token_returns_none_when_unset(student, db_session):
    from app.tasks.push_notifications import _get_fcm_token

    stu, _ = student
    # fcm_token is None by default

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.push_notifications.AsyncSessionLocal", return_value=ctx):
        token = await _get_fcm_token(str(stu.id))

    assert token is None


# --- send_push Celery task (sync — patches asyncio.run) ---

def test_send_push_skips_when_no_token():
    with patch("app.tasks.push_notifications.asyncio") as mock_asyncio, \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_asyncio.run.return_value = None  # no token

        from app.tasks.push_notifications import send_push

        send_push.apply(args=["user-id", "Title", "Body", {"type": "test"}])

        mock_ps.send_push_notification.assert_not_called()


def test_send_push_calls_push_service_with_token():
    with patch("app.tasks.push_notifications.asyncio") as mock_asyncio, \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_asyncio.run.return_value = "device-token-xyz"

        from app.tasks.push_notifications import send_push

        send_push.apply(args=["user-id", "Title", "Body", {"type": "new_case", "session_id": "s1"}])

        mock_ps.send_push_notification.assert_called_once_with(
            "device-token-xyz",
            "Title",
            "Body",
            {"type": "new_case", "session_id": "s1"},
        )


def test_send_push_retries_on_exception():
    with patch("app.tasks.push_notifications.asyncio") as mock_asyncio, \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_asyncio.run.return_value = "token"
        mock_ps.send_push_notification.side_effect = Exception("FCM unavailable")

        from app.tasks.push_notifications import send_push
        from celery.exceptions import Retry

        # apply() with raises=True surfaces Celery's Retry exception on the first attempt.
        with pytest.raises(Retry):
            send_push.apply(args=["user-id", "t", "b", {}], throw=True)
```

- [ ] **Step 6: Run tests to verify they fail**

```bash
uv run pytest tests/test_push_task.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tasks'`.

- [ ] **Step 7: Create app/tasks/__init__.py**

```bash
mkdir -p /path/to/backend/app/tasks
touch app/tasks/__init__.py
```

Actually, create the file:

Create `app/tasks/__init__.py` (empty file).

- [ ] **Step 8: Create app/tasks/push_notifications.py**

Create `app/tasks/push_notifications.py`:

```python
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import push_service


async def _get_fcm_token(user_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User.fcm_token).where(User.id == uuid.UUID(user_id))
        )
        return result.scalar_one_or_none()


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def send_push(
    self,
    user_id: str,
    title: str,
    body: str,
    data: dict[str, str],
) -> None:
    token = asyncio.run(_get_fcm_token(user_id))
    if not token:
        return
    try:
        push_service.send_push_notification(token, title, body, data)
    except Exception as exc:
        raise self.retry(exc=exc)
```

- [ ] **Step 9: Run tests to verify they pass**

```bash
uv run pytest tests/test_push_service.py tests/test_push_task.py -v
```
Expected: PASS (all 7 tests).

- [ ] **Step 10: Commit**

```bash
git add app/services/push_service.py app/tasks/__init__.py app/tasks/push_notifications.py \
        tests/test_push_service.py tests/test_push_task.py
git commit -m "feat: push_service + send_push Celery task"
```

---

## Task 4: Nudge stub + case initiation tasks

**Files:**
- Create: `app/tasks/nudge.py`
- Create: `app/tasks/case_initiation.py`
- Create: `tests/test_case_initiation.py`

**Note:** Depends on Task 3 (`send_push` must exist).

- [ ] **Step 1: Write failing tests**

Create `tests/test_case_initiation.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole

pytestmark = pytest.mark.usefixtures("clean_tables")

_NUDGE = {"frequency": "low", "tone": "neutral", "example": ""}


@pytest_asyncio.fixture
async def ci_setup(professor, student, db_session):
    """Course with a released unit, enrolled student, no active session."""
    prof, _ = professor
    stu, _ = student
    course = Course(
        title="CI Course",
        professor_id=prof.id,
        class_code="CIC001",
        is_active=True,
        msg_window_start=time(0, 0),   # midnight — window is always open in tests
        msg_window_end=time(23, 59),
        msg_timezone="UTC",
    )
    db_session.add(course)
    await db_session.flush()

    unit = Unit(
        course_id=course.id,
        label="Unit 1",
        status=UnitStatus.released,
        release_date=datetime.now(timezone.utc),
    )
    db_session.add(unit)
    await db_session.flush()

    disease = Disease(
        unit_id=unit.id,
        name="MDD",
        category="Mood",
        key_symptoms=["low mood"],
        differentials=["GAD"],
        difficulty_tier=2,
        speech_style="flat",
        nudge_behavior=_NUDGE,
    )
    db_session.add(disease)
    await db_session.flush()

    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(stu)
    return stu, course, disease


# --- _fetch_eligible_pairs ---

async def test_fetch_eligible_pairs_returns_eligible_student(ci_setup, db_session):
    from app.tasks.case_initiation import _fetch_eligible_pairs

    stu, course, _ = ci_setup

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx):
        pairs = await _fetch_eligible_pairs()

    assert any(uid == stu.id and cid == course.id for uid, cid, _ in pairs)


async def test_fetch_eligible_pairs_skips_student_with_active_session(
    ci_setup, db_session
):
    from app.tasks.case_initiation import _fetch_eligible_pairs

    stu, course, disease = ci_setup
    session = Session(
        disease_id=disease.id,
        user_id=stu.id,
        course_id=course.id,
        started_at=datetime.now(timezone.utc),
        status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx):
        pairs = await _fetch_eligible_pairs()

    assert not any(uid == stu.id for uid, cid, _ in pairs)


async def test_fetch_eligible_pairs_skips_outside_window(ci_setup, db_session):
    from app.tasks.case_initiation import _fetch_eligible_pairs

    _, course, _ = ci_setup
    # Close the window so no time qualifies
    course.msg_window_start = time(0, 0)
    course.msg_window_end = time(0, 1)  # 00:00–00:01 — almost never matches
    db_session.add(course)
    await db_session.commit()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    # Force "now" to be outside the window (e.g. 12:00 UTC)
    fixed_now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx), \
         patch("app.tasks.case_initiation.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.combine = datetime.combine
        pairs = await _fetch_eligible_pairs()

    assert pairs == []


# --- _check_and_create ---

async def test_check_and_create_creates_session_when_none_exists(
    ci_setup, db_session
):
    from app.tasks.case_initiation import _check_and_create

    stu, course, _ = ci_setup

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.id = uuid.uuid4()

    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx), \
         patch("app.tasks.case_initiation.session_service") as mock_ss:
        mock_ss.create_new_session = AsyncMock(return_value=(mock_session, MagicMock()))
        result = await _check_and_create(str(stu.id), str(course.id))

    assert result == mock_session.id
    mock_ss.create_new_session.assert_called_once()


async def test_check_and_create_returns_none_when_session_exists(
    ci_setup, db_session
):
    from app.tasks.case_initiation import _check_and_create

    stu, course, disease = ci_setup
    session = Session(
        disease_id=disease.id,
        user_id=stu.id,
        course_id=course.id,
        started_at=datetime.now(timezone.utc),
        status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.commit()

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db_session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("app.tasks.case_initiation.AsyncSessionLocal", return_value=ctx), \
         patch("app.tasks.case_initiation.session_service") as mock_ss:
        result = await _check_and_create(str(stu.id), str(course.id))

    assert result is None
    mock_ss.create_new_session.assert_not_called()


# --- check_and_initiate_cases (sync task, mock asyncio.run + Redis) ---

def test_check_and_initiate_cases_dispatches_for_eligible_students():
    user_id = uuid.uuid4()
    course_id = uuid.uuid4()
    window_end = datetime(2026, 6, 4, 22, 0, 0, tzinfo=timezone.utc)

    with patch("app.tasks.case_initiation.asyncio") as mock_asyncio, \
         patch("app.tasks.case_initiation.sync_redis") as mock_redis_mod, \
         patch("app.tasks.case_initiation.initiate_case") as mock_task:
        mock_asyncio.run.return_value = [(user_id, course_id, window_end)]

        mock_r = MagicMock()
        mock_r.set.return_value = True  # key was newly set (not a duplicate)
        mock_redis_mod.from_url.return_value = mock_r

        from app.tasks.case_initiation import check_and_initiate_cases
        check_and_initiate_cases()

        mock_task.apply_async.assert_called_once()
        call_kwargs = mock_task.apply_async.call_args
        assert call_kwargs.kwargs["args"] == [str(user_id), str(course_id)]


def test_check_and_initiate_cases_skips_duplicate_via_redis():
    user_id = uuid.uuid4()
    course_id = uuid.uuid4()
    window_end = datetime(2026, 6, 4, 22, 0, 0, tzinfo=timezone.utc)

    with patch("app.tasks.case_initiation.asyncio") as mock_asyncio, \
         patch("app.tasks.case_initiation.sync_redis") as mock_redis_mod, \
         patch("app.tasks.case_initiation.initiate_case") as mock_task:
        mock_asyncio.run.return_value = [(user_id, course_id, window_end)]

        mock_r = MagicMock()
        mock_r.set.return_value = False  # key already exists — duplicate
        mock_redis_mod.from_url.return_value = mock_r

        from app.tasks.case_initiation import check_and_initiate_cases
        check_and_initiate_cases()

        mock_task.apply_async.assert_not_called()


# --- initiate_case (sync task) ---

def test_initiate_case_creates_session_and_sends_push():
    session_id = uuid.uuid4()

    with patch("app.tasks.case_initiation.asyncio") as mock_asyncio, \
         patch("app.tasks.case_initiation.send_push") as mock_push:
        mock_asyncio.run.return_value = session_id

        from app.tasks.case_initiation import initiate_case
        initiate_case(str(uuid.uuid4()), str(uuid.uuid4()))

        mock_push.delay.assert_called_once()
        push_args = mock_push.delay.call_args.args
        assert push_args[3]["type"] == "new_case"
        assert push_args[3]["session_id"] == str(session_id)


def test_initiate_case_skips_push_when_session_already_exists():
    with patch("app.tasks.case_initiation.asyncio") as mock_asyncio, \
         patch("app.tasks.case_initiation.send_push") as mock_push:
        mock_asyncio.run.return_value = None  # already had a session

        from app.tasks.case_initiation import initiate_case
        initiate_case(str(uuid.uuid4()), str(uuid.uuid4()))

        mock_push.delay.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_case_initiation.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tasks.nudge'` or similar.

- [ ] **Step 3: Create app/tasks/nudge.py**

Create `app/tasks/nudge.py`:

```python
from __future__ import annotations

import logging

from app.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task
def check_and_send_nudges() -> None:
    logger.info("check_and_send_nudges: not yet implemented")
```

- [ ] **Step 4: Create app/tasks/case_initiation.py**

Create `app/tasks/case_initiation.py`:

```python
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import redis as sync_redis
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.celery_app import celery
from app.config import settings
from app.database import AsyncSessionLocal
from app.models.course import Course
from app.models.enrollment import Enrollment
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from app.models.user import User, UserRole
from app.services import session_service
from app.tasks.push_notifications import send_push

logger = logging.getLogger(__name__)


async def _fetch_eligible_pairs() -> list[tuple[uuid.UUID, uuid.UUID, datetime]]:
    """Return (user_id, course_id, window_end_utc) for each eligible student."""
    async with AsyncSessionLocal() as db:
        courses_q = (
            select(Course)
            .join(Unit, Unit.course_id == Course.id)
            .where(Course.is_active == True, Unit.status == UnitStatus.released)  # noqa: E712
            .distinct()
        )
        courses = list((await db.execute(courses_q)).scalars().all())

        now_utc = datetime.now(timezone.utc)
        eligible: list[tuple[uuid.UUID, uuid.UUID, datetime]] = []

        for course in courses:
            tz = ZoneInfo(course.msg_timezone)
            now_local = now_utc.astimezone(tz)

            if not (course.msg_window_start <= now_local.time() < course.msg_window_end):
                continue

            window_end_naive = datetime.combine(now_local.date(), course.msg_window_end)
            window_end_local = window_end_naive.replace(tzinfo=tz)
            window_end_utc = window_end_local.astimezone(timezone.utc)

            if window_end_utc <= now_utc:
                continue

            active_session_exists = (
                select(Session.id)
                .where(
                    Session.user_id == User.id,
                    Session.course_id == course.id,
                    Session.status == SessionStatus.active,
                )
                .correlate(User)
                .exists()
            )
            students_q = (
                select(User)
                .join(Enrollment, Enrollment.user_id == User.id)
                .where(
                    Enrollment.course_id == course.id,
                    User.role == UserRole.student,
                    ~active_session_exists,
                )
            )
            students = list((await db.execute(students_q)).scalars().all())

            for student in students:
                eligible.append((student.id, course.id, window_end_utc))

        return eligible


def _random_eta(window_end_utc: datetime) -> datetime:
    now_utc = datetime.now(timezone.utc)
    remaining = (window_end_utc - now_utc).total_seconds()
    if remaining <= 0:
        return now_utc
    delay = random.uniform(0, remaining)
    return now_utc + timedelta(seconds=delay)


@celery.task
def check_and_initiate_cases() -> None:
    pairs = asyncio.run(_fetch_eligible_pairs())
    if not pairs:
        return

    r = sync_redis.from_url(settings.redis_url, decode_responses=True)
    try:
        for user_id, course_id, window_end_utc in pairs:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            dedup_key = f"initiate:{course_id}:{user_id}:{today}"
            ttl = max(1, int((window_end_utc - datetime.now(timezone.utc)).total_seconds()))
            if r.set(dedup_key, "1", nx=True, ex=ttl):
                eta = _random_eta(window_end_utc)
                initiate_case.apply_async(
                    args=[str(user_id), str(course_id)], eta=eta
                )
    finally:
        r.close()


async def _check_and_create(
    user_id_str: str, course_id_str: str
) -> uuid.UUID | None:
    """Create a session for the student if they still have none. Returns session.id or None."""
    user_id = uuid.UUID(user_id_str)
    course_id = uuid.UUID(course_id_str)
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(Session.id).where(
                    Session.user_id == user_id,
                    Session.course_id == course_id,
                    Session.status == SessionStatus.active,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return None
        session, _ = await session_service.create_new_session(user_id, course_id, db)
        return session.id


@celery.task
def initiate_case(user_id: str, course_id: str) -> None:
    try:
        session_id = asyncio.run(_check_and_create(user_id, course_id))
    except HTTPException as exc:
        logger.warning(
            "initiate_case: skipping user=%s course=%s — %s",
            user_id, course_id, exc.detail,
        )
        return
    except IntegrityError:
        logger.info(
            "initiate_case: IntegrityError (race) user=%s course=%s", user_id, course_id
        )
        return

    if session_id is None:
        return

    send_push.delay(
        user_id,
        "PocketPatient",
        "A new patient is reaching out to you",
        {"type": "new_case", "session_id": str(session_id)},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_case_initiation.py -v
```
Expected: PASS (all 9 tests).

- [ ] **Step 6: Commit**

```bash
git add app/tasks/nudge.py app/tasks/case_initiation.py tests/test_case_initiation.py
git commit -m "feat: case initiation scheduler + nudge stub"
```

---

## Task 5: Dispatch push from session_service on bot reply

**Files:**
- Modify: `app/services/session_service.py`
- Modify: `tests/test_sessions_router.py`

**Note:** Depends on Task 3 (`send_push` must exist at `app.tasks.push_notifications`).

- [ ] **Step 1: Write the failing test**

In `tests/test_sessions_router.py`, add the following test at the end of the file (it needs the existing `setup` fixture and imports):

```python
async def test_send_message_dispatches_push_notification(client, setup, db_session):
    import app.tasks.push_notifications as _push_mod
    from unittest.mock import patch, MagicMock

    _, _, stu, stu_token, course, disease = setup

    # Create an active session with an opening message
    session = Session(
        disease_id=disease.id,
        user_id=stu.id,
        course_id=course.id,
        started_at=datetime.now(timezone.utc),
        status=SessionStatus.active,
    )
    db_session.add(session)
    await db_session.flush()
    db_session.add(Message(
        session_id=session.id,
        role=MessageRole.patient,
        content="Hi there.",
        sent_at=datetime.now(timezone.utc),
        is_nudge=False,
    ))
    await db_session.commit()

    mock_push = MagicMock()
    with patch.object(_push_mod, "send_push", mock_push), \
         patch("app.services.session_service.gateway") as mock_gw:
        mock_gw.generate_patient_message = AsyncMock(return_value="I feel tired.")
        resp = await client.post(
            f"/api/v1/sessions/{session.id}/messages",
            json={"content": "How long have you felt this way?"},
            headers={"Authorization": f"Bearer {stu_token}"},
        )

    assert resp.status_code == 200
    mock_push.delay.assert_called_once()
    call_args = mock_push.delay.call_args.args
    assert call_args[0] == str(stu.id)
    assert call_args[3]["type"] == "new_message"
    assert call_args[3]["session_id"] == str(session.id)
```

- [ ] **Step 2: Run the new test to verify it fails**

```bash
uv run pytest tests/test_sessions_router.py::test_send_message_dispatches_push_notification -v
```
Expected: FAIL — test passes or `mock_push.delay.assert_called_once()` fails because the dispatch isn't implemented yet.

- [ ] **Step 3: Modify session_service.py to dispatch the push**

In `app/services/session_service.py`, locate `send_student_message_and_get_reply`. After the final `await db.refresh(patient_msg)` line and before `return patient_msg`, add:

```python
    try:
        import app.tasks.push_notifications as _push_mod
        _push_mod.send_push.delay(
            str(session.user_id),
            "PocketPatient",
            "Your patient replied",
            {"type": "new_message", "session_id": str(session.id)},
        )
    except Exception:
        pass

    return patient_msg
```

The local module import (rather than `from app.tasks.push_notifications import send_push`) keeps the reference on the module object, which is what `patch.object` targets in tests. The `try/except Exception` ensures a Redis connectivity issue never raises inside the HTTP request.

- [ ] **Step 4: Run the new test to verify it passes**

```bash
uv run pytest tests/test_sessions_router.py::test_send_message_dispatches_push_notification -v
```
Expected: PASS.

- [ ] **Step 5: Run the full sessions router test file**

```bash
uv run pytest tests/test_sessions_router.py -v
```
Expected: PASS (all existing tests + new test). The `try/except` wrapper means tests that don't patch the push task still pass (the push dispatch silently fails — no Celery broker in the test environment).

- [ ] **Step 6: Commit**

```bash
git add app/services/session_service.py tests/test_sessions_router.py
git commit -m "feat: dispatch send_push after bot reply in session_service"
```

---

## Task 6: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

```bash
uv run pytest -v
```
Expected: all tests PASS.

- [ ] **Step 2: Verify app imports cleanly**

```bash
uv run python -c "from app.main import app; from app.celery_app import celery; print('ok')"
```
Expected: `ok` with no import errors.

- [ ] **Step 3: Verify Celery worker starts**

(Requires Redis running locally or via docker-compose.)
```bash
uv run celery -A app.celery_app worker --loglevel=info --without-heartbeat -P solo &
sleep 3 && kill %1 2>/dev/null; true
```
Expected: worker starts, logs `[tasks]` listing `app.tasks.case_initiation.check_and_initiate_cases`, `app.tasks.case_initiation.initiate_case`, `app.tasks.nudge.check_and_send_nudges`, `app.tasks.push_notifications.send_push`. Then killed cleanly.

- [ ] **Step 4: Update API contract docs**

In `docs/api-contract.md`, add the `PUT /users/me/fcm-token` row to the Users endpoint table and a brief detail block.

Find the Users section table and add:
```markdown
| PUT | `/api/v1/users/me/fcm-token` | Register or refresh FCM device token | Bearer JWT (any role) | ✅ Week 9 |
```

Add a detail block after the existing `/me/role` detail block:
```markdown
### PUT /api/v1/users/me/fcm-token
**Role required:** any authenticated user
**Request:** `{"fcm_token": "<Firebase device token string>"}`
**Response:** `UserOut` (200) — `fcm_token` is write-only and intentionally not included in the response shape.
**Behavior:** Idempotent — repeated calls overwrite the token (handles Flutter token refresh).
**Errors:** 401 unauthenticated, 422 if `fcm_token` is empty string.
```

- [ ] **Step 5: Commit**

```bash
git add docs/api-contract.md
git commit -m "docs: document PUT /users/me/fcm-token endpoint"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Goal 1 (Celery + Redis): Task 1 installs `celery>=5.3,<6`, creates `celery_app.py` with broker/backend wired to `settings.redis_url`.
- ✅ Goal 2 (beat schedule): `celery_app.py` registers both beat tasks at 900s and 3600s.
- ✅ Goal 3 (case initiation): Task 4 implements `check_and_initiate_cases` with messaging window check, Redis dedup, ETA randomisation, and `initiate_case` with race-condition guard.
- ✅ Goal 4 (fcm_token + endpoint): Task 2 adds the column, migration, and `PUT /users/me/fcm-token`.
- ✅ Goal 5 (send_push task): Task 3 creates `push_service.py` and `push_notifications.py` with retry.
- ✅ Goal 6 (patient-reply push): Task 5 dispatches from `session_service` after commit.
- ✅ `app/services/firebase.py` shared init: Task 1 creates it and wires FastAPI + Celery worker.
- ✅ `docker-compose.yml` services: Task 1.
- ✅ Nudge stub: Task 4.
- ✅ Redis dedup guard: `check_and_initiate_cases` uses `r.set(key, "1", nx=True, ex=ttl)`.
- ✅ `create_new_session` return tuple: `_check_and_create` unpacks `session, _ = await ...`.
- ✅ Firebase not initialized in worker: `worker_process_init` signal in `celery_app.py`.

**Placeholder scan:** No TBDs or incomplete steps found.

**Type consistency check:**
- `send_push(self, user_id: str, title: str, body: str, data: dict[str, str])` — used as `send_push.delay(str(user_id), "PocketPatient", ..., {"type": ..., "session_id": str(...)})` in Tasks 4 and 5. ✅
- `_get_fcm_token(user_id: str) -> str | None` — called via `asyncio.run(_get_fcm_token(user_id))` in Task 3. ✅
- `_check_and_create(user_id_str, course_id_str) -> uuid.UUID | None` — called via `asyncio.run(...)` in `initiate_case`. ✅
- `_fetch_eligible_pairs() -> list[tuple[uuid.UUID, uuid.UUID, datetime]]` — returned pairs unpacked as `user_id, course_id, window_end_utc` in `check_and_initiate_cases`. ✅
- `session_service.create_new_session(user_id, course_id, db) -> tuple[Session, Message]` — unpacked as `session, _` in `_check_and_create`. ✅
