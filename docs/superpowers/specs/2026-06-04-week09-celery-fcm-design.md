# Week 9 Backend Design — Celery Scheduler + FCM Push Notifications

**Date:** 2026-06-04
**Phase:** 2 — Core Simulation
**Theme:** Async case initiation via Celery Beat + Firebase Cloud Messaging (FCM) push notifications.

---

## Goals

1. Set up Celery + Redis as the task broker/backend.
2. Create a Celery Beat schedule that fires `check_and_initiate_cases` every 15 minutes and `check_and_send_nudges` every hour.
3. Implement the case-initiation task: find eligible students, schedule a randomly-timed `initiate_case` task within the course's messaging window, and send a "new patient" push notification.
4. Add `fcm_token` to the users table + a `PUT /api/v1/users/me/fcm-token` endpoint for token registration.
5. Build a reusable `send_push` Celery task backed by a thin `push_service` Firebase wrapper.
6. Dispatch a "patient replied" push from `session_service` after the bot reply is committed.

## Non-goals

- Nudge task logic (registered as a stub this week; full implementation next sprint).
- Frontend FCM token registration and notification handling (Dev B scope).
- Per-course configurable push notification copy.
- Retry/DLQ tuning for `send_push` beyond Celery defaults.

---

## Architecture

### New files

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `app/celery_app.py` | Celery instance + beat schedule |
| Create | `app/tasks/__init__.py` | Empty package marker |
| Create | `app/tasks/case_initiation.py` | `check_and_initiate_cases` (beat) + `initiate_case` (delayed) |
| Create | `app/tasks/nudge.py` | `check_and_send_nudges` stub |
| Create | `app/tasks/push_notifications.py` | `send_push` Celery task |
| Create | `app/services/push_service.py` | Thin `firebase_admin.messaging` wrapper |
| Modify | `app/models/user.py` | Add `fcm_token: Mapped[str \| None]` |
| Create | `alembic/versions/*_add_fcm_token_to_users.py` | Migration |
| Modify | `app/routers/users.py` | `PUT /users/me/fcm-token` |
| Modify | `app/services/session_service.py` | Dispatch `send_push.delay` after bot reply |
| Modify | `docker-compose.yml` | Add `celery-worker` and `celery-beat` services |
| Modify | `pyproject.toml` | Add `celery[redis]` |

### Data flows

**Case initiation (scheduled):**
```
Celery Beat (every 15 min)
  → check_and_initiate_cases()
    → query: active courses with ≥1 released unit
    → for each course: find enrolled students with no active session
    → if now (in course timezone) is within msg_window_start..msg_window_end
      → for each eligible student:
          pick a random datetime in the remaining window today
          → initiate_case.apply_async(args=[user_id, course_id], eta=random_dt)
              → double-check: student still has no active session
              → asyncio.run(session_service.create_new_session(user_id, course_id, db))
              → send_push.delay(user_id, "New patient", "A new patient is reaching out to you",
                                {"type": "new_case", "session_id": str(session.id)})
```

**Push on patient reply (HTTP request path):**
```
POST /sessions/{id}/messages
  → session_service.send_student_message_and_get_reply(...)
    → LLM reply committed to DB
    → send_push.delay(str(user_id), "PocketPatient", "Your patient replied",
                      {"type": "new_message", "session_id": str(session_id)})
    → return patient_msg
```

---

## `app/celery_app.py`

```python
from celery import Celery
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
```

The `celery` instance is imported by tasks (`from app.celery_app import celery`) and by `docker-compose` entrypoints.

---

## Data model: `users.fcm_token`

Add one nullable column to `app/models/user.py`:

```python
fcm_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

Migration adds the column with `ALTER TABLE users ADD COLUMN fcm_token VARCHAR(512)`.

---

## `PUT /api/v1/users/me/fcm-token`

- **Auth:** any authenticated user (`get_current_user`).
- **Body:** `{"fcm_token": "<device token string>"}`
- **Behavior:** set `user.fcm_token = body.fcm_token`, commit, return `UserOut`.
- **Idempotent:** repeated calls overwrite the stored token (token refresh from the Flutter client re-registers silently).
- **Errors:** 422 if `fcm_token` is empty string.

---

## `app/services/push_service.py`

Thin wrapper; keeps `firebase_admin` import isolated in one place.

```python
def send_push_notification(token: str, title: str, body: str, data: dict[str, str]) -> None:
    message = firebase_admin.messaging.Message(
        token=token,
        notification=firebase_admin.messaging.Notification(title=title, body=body),
        data=data,
    )
    firebase_admin.messaging.send(message)
```

- Raises `firebase_admin.exceptions.FirebaseError` on failure — callers decide whether to retry.
- `data` values must all be strings (FCM requirement); callers are responsible for this.

---

## `app/tasks/push_notifications.py`

```python
@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def send_push(self, user_id: str, title: str, body: str, data: dict[str, str]) -> None:
    token = asyncio.run(_get_fcm_token(user_id))
    if not token:
        return  # user has no registered device — silently skip
    try:
        push_service.send_push_notification(token, title, body, data)
    except Exception as exc:
        raise self.retry(exc=exc)
```

`_get_fcm_token(user_id)` opens its own `AsyncSessionLocal` session, queries `users.fcm_token`, and closes the session. Uses `asyncio.run()` per the agreed bridge pattern.

---

## `app/tasks/case_initiation.py`

### `check_and_initiate_cases` (beat task)

1. Open a DB session via `asyncio.run()`.
2. Query all active courses that have at least one `released` unit.
3. For each course, query enrolled students (`UserRole.student`) who have no `active` session for that course.
4. Compute `now` in the course's timezone using `zoneinfo.ZoneInfo(course.msg_timezone)`.
5. If `now.time()` is within `[msg_window_start, msg_window_end)`:
   - Compute `window_end_dt` = today's date at `msg_window_end` in course tz, converted to UTC.
   - For each eligible student, pick a random UTC datetime between `now_utc` and `window_end_dt`.
   - Dispatch `initiate_case.apply_async(args=[str(user_id), str(course_id)], eta=random_dt)`.

### `initiate_case(user_id: str, course_id: str)` (delayed task)

1. `asyncio.run(_check_and_create(user_id, course_id))`:
   - Open DB session.
   - Re-check: student has no active session (race-condition guard).
   - If still eligible: call `session_service.create_new_session(uuid(user_id), uuid(course_id), db)`.
   - Return `session.id`.
2. If a session was created: `send_push.delay(user_id, "PocketPatient", "A new patient is reaching out to you", {"type": "new_case", "session_id": str(session_id)})`.

**Race-condition note:** the partial-index `uq_one_active_session_per_user_course` on `sessions` enforces uniqueness at the DB level even if two workers fire simultaneously. The re-check is a best-effort fast path; the constraint is the real guard.

---

## `app/tasks/nudge.py`

```python
@celery.task
def check_and_send_nudges() -> None:
    import logging
    logging.getLogger(__name__).info("check_and_send_nudges: not yet implemented")
```

---

## `session_service` modification

After `await db.commit()` on the patient reply (end of `send_student_message_and_get_reply`), add:

```python
from app.tasks.push_notifications import send_push
send_push.delay(
    str(session.user_id),
    "PocketPatient",
    "Your patient replied",
    {"type": "new_message", "session_id": str(session.id)},
)
```

Import is a local import inside the function to avoid circular import between `session_service` and `celery_app`. The `.delay()` call is wrapped in `try/except Exception` so a Redis connectivity issue never raises inside the HTTP request and rolls back the committed reply.

---

## `docker-compose.yml` additions

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

---

## Error handling

| Scenario | Behavior |
|----------|----------|
| Student has no FCM token | `send_push` skips silently (no token = no device registered) |
| Firebase send fails | `send_push` retries up to 3× with 60s delay; after that, task fails silently (push is best-effort) |
| `create_new_session` raises (no disease pool) | `initiate_case` logs the exception, does not retry (course data issue, not transient) |
| Race: two workers fire `initiate_case` for same student | DB unique partial index raises `IntegrityError`; task catches and exits cleanly |
| Celery worker unreachable at HTTP request time | `send_push.delay()` call itself may raise a connection error; wrap in try/except so it never blows up a student's send-message request |

---

## Testing

All Celery tasks are tested by calling the underlying `_check_and_create` / `_get_fcm_token` async helpers directly (via `asyncio.run` or `pytest-asyncio`), not by spinning up a real worker. The `.delay()` call in `session_service` is mocked via `patch("app.services.session_service.send_push")`.

### New test files

| File | What it tests |
|------|---------------|
| `tests/test_push_service.py` | `send_push_notification` calls `firebase_admin.messaging.send` with correct `Message` shape; `FirebaseError` propagates |
| `tests/test_push_task.py` | `send_push` task: skips when no token; calls `push_service.send_push_notification`; retries on exception |
| `tests/test_case_initiation.py` | `check_and_initiate_cases`: dispatches tasks for eligible students, skips students with active sessions, skips outside window; `initiate_case`: calls `create_new_session`, dispatches push, skips when session already exists |
| `tests/test_fcm_token_endpoint.py` | `PUT /users/me/fcm-token`: sets token, overwrites on repeat call, 422 on empty string |

### Modified test files

| File | Change |
|------|--------|
| `tests/test_sessions_router.py` | Assert `send_push.delay` called after bot reply (mock the task) |

---

## Open questions resolved

- **Async bridge:** `asyncio.run()` wrappers in sync Celery tasks.
- **Nudge task:** beat registered, body is a stub.
- **Push on patient reply:** Celery task dispatched fire-and-forget; never blocks the HTTP response.
- **Timezone handling:** Python 3.11 built-in `zoneinfo.ZoneInfo`.
- **FCM token missing:** silent skip (device not yet registered is not an error).
