# Week 12 Backend — E2E Simulation, Error Recovery, Performance Baseline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out Phase 2 with a deterministic end-to-end simulation test suite, resilience against LLM/Celery/FCM failures, and latency instrumentation + indexes for a performance baseline.

**Architecture:** Add a loop-safe Celery task runner so tasks can run under eager mode in tests; harden the LLM gateway, the bot-reply task, the push task, and the Celery base task; add observability (LLM/DB latency logs, a slow-query listener) and one index; finally build the e2e suite that exercises the whole lifecycle offline with a mocked gateway and eager Celery.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async (asyncpg), Celery 5 (Redis broker), Pydantic v2, pytest + pytest-asyncio (`asyncio_mode=auto`), Alembic, firebase-admin, google-genai.

**Spec:** `docs/superpowers/specs/2026-06-21-week12-e2e-error-recovery-perf-design.md`

## Global Constraints

- Python 3.11+; run everything via `uv` (`uv run pytest`, `uv run alembic ...`).
- Tests use the `clean_tables` + `professor`/`student`/`db_session` fixtures from `tests/conftest.py`. Never call `_truncate_all()` in teardown.
- Ownership checks return 404, not 403.
- Celery broker is Redis — "dead letter" = log-on-final-failure, not a broker DLQ.
- Current Alembic head: `4522b2be2830` (new migration's `down_revision`).
- `firebase_admin.messaging.UnregisteredError` and `.SenderIdMismatchError` both exist and are the token-dead signals.
- Structured logs are single-line `json.dumps({...})` with an `"event"` key (match `app/middleware/logging.py`).

---

### Task 1: Loop-safe Celery task runner

Replace the bare `asyncio.run(...)` in every Celery task entrypoint with a runner that executes the coroutine on a dedicated thread with its own event loop. This is required so tasks can run under `task_always_eager` inside the test's running event loop (Task 8), and is harmless in the prefork worker. The four existing task unit-test files patch `app.tasks.<mod>.asyncio`; they must be repointed at the new `run_task_async` seam.

**Files:**
- Create: `app/tasks/_run.py`
- Test: `tests/test_run_task_async.py`
- Modify: `app/tasks/bot_reply.py`, `app/tasks/nudge.py`, `app/tasks/case_initiation.py`, `app/tasks/push_notifications.py`
- Modify (repoint patch seam): `tests/test_bot_reply.py`, `tests/test_nudge.py`, `tests/test_case_initiation.py`, `tests/test_push_task.py`

**Interfaces:**
- Produces: `app.tasks._run.run_task_async(coro) -> Any` — runs `coro` to completion on a fresh thread/loop, returns its result or re-raises its exception.

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_task_async.py`:

```python
from __future__ import annotations

import asyncio

import pytest


def test_run_task_async_returns_result():
    from app.tasks._run import run_task_async

    async def coro():
        return 42

    assert run_task_async(coro()) == 42


def test_run_task_async_propagates_exception():
    from app.tasks._run import run_task_async

    async def coro():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        run_task_async(coro())


async def test_run_task_async_works_inside_running_loop():
    # asyncio_mode=auto means a loop is already running here; bare asyncio.run
    # would raise. run_task_async must still work.
    from app.tasks._run import run_task_async

    async def coro():
        return "ok"

    assert run_task_async(coro()) == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_run_task_async.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tasks._run'`

- [ ] **Step 3: Write the implementation**

Create `app/tasks/_run.py`:

```python
from __future__ import annotations

import asyncio
import threading
from typing import Any
from collections.abc import Coroutine


def run_task_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run `coro` on a dedicated thread with its own event loop.

    Celery task bodies call this instead of asyncio.run(). Running on a fresh
    thread keeps each task's event loop isolated (so the per-task pool reset in
    app.celery_app still applies) and, crucially, works even when a loop is
    already running in the calling thread — which is the case under Celery's
    task_always_eager mode inside pytest-asyncio tests.
    """
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
            box["exc"] = exc

    thread = threading.Thread(target=_runner)
    thread.start()
    thread.join()
    if "exc" in box:
        raise box["exc"]
    return box["result"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_run_task_async.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Refactor the four task entrypoints**

In `app/tasks/bot_reply.py`: add `from app.tasks._run import run_task_async` to the imports and change the entrypoint body:

```python
@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def generate_and_send_reply(self, session_id: str) -> None:
    try:
        run_task_async(_generate_and_send(session_id, self.request.id))
    except Exception as exc:
        raise self.retry(exc=exc)
```

In `app/tasks/nudge.py`: add the import and change:

```python
@celery.task
def check_and_send_nudges() -> None:
    run_task_async(_run_nudge_check())
```

In `app/tasks/case_initiation.py`: add the import and change both spots:

```python
@celery.task
def check_and_initiate_cases() -> None:
    pairs = run_task_async(_fetch_eligible_pairs())
    ...

@celery.task
def initiate_case(user_id: str, course_id: str) -> None:
    try:
        session_id = run_task_async(_check_and_create(user_id, course_id))
    except HTTPException as exc:
        ...
```

In `app/tasks/push_notifications.py`: add the import and change:

```python
@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def send_push(self, user_id, title, body, data):
    token = run_task_async(_get_fcm_token(user_id))
    ...
```

Leave the `import asyncio` line in `case_initiation.py`/`bot_reply.py`/`nudge.py` if other code still uses it; otherwise remove. (`push_notifications.py` no longer needs `asyncio` — remove its import.)

- [ ] **Step 6: Repoint the existing task-test patch seams**

These tests patch `app.tasks.<mod>.asyncio` and assert on `asyncio.run`. The execution seam is now `run_task_async`. In each, replace `patch("app.tasks.<mod>.asyncio") as mock_asyncio` with `patch("app.tasks.<mod>.run_task_async") as mock_run`, replace `mock_asyncio.run.return_value` → `mock_run.return_value`, `mock_asyncio.run.side_effect` → `mock_run.side_effect`, and `mock_asyncio.run.assert_called_once_with(...)` → `mock_run.assert_called_once_with(...)`.

Affected sites:
- `tests/test_bot_reply.py`: `test_generate_and_send_reply_invokes_helper_with_session_and_task_id`, `test_generate_and_send_reply_retries_on_exception`.
- `tests/test_nudge.py`: `test_check_and_send_nudges_invokes_run_check`.
- `tests/test_case_initiation.py`: all six `check_and_initiate_cases` / `initiate_case` tests.
- `tests/test_push_task.py`: `test_send_push_skips_when_no_token`, `test_send_push_calls_push_service_with_token`, `test_send_push_retries_on_exception`.

Example (`tests/test_bot_reply.py`):

```python
def test_generate_and_send_reply_invokes_helper_with_session_and_task_id():
    mock_helper = MagicMock(return_value="coro-sentinel")
    with patch("app.tasks.bot_reply.run_task_async") as mock_run, \
         patch("app.tasks.bot_reply._generate_and_send", mock_helper):
        mock_run.return_value = None

        from app.tasks.bot_reply import generate_and_send_reply
        generate_and_send_reply.apply(args=["session-id-str"], task_id="my-task-id")

        mock_helper.assert_called_once_with("session-id-str", "my-task-id")
        mock_run.assert_called_once_with("coro-sentinel")
```

- [ ] **Step 7: Run the full suite to verify green**

Run: `uv run pytest -v`
Expected: PASS (all existing tests + the 3 new runner tests)

- [ ] **Step 8: Commit**

```bash
git add app/tasks/_run.py tests/test_run_task_async.py app/tasks/bot_reply.py app/tasks/nudge.py app/tasks/case_initiation.py app/tasks/push_notifications.py tests/test_bot_reply.py tests/test_nudge.py tests/test_case_initiation.py tests/test_push_task.py
git commit -m "refactor: loop-safe run_task_async for Celery task entrypoints"
```

---

### Task 2: LLM gateway — retry with backoff + latency logging

Wrap `_generate_content` in a retry loop (sleeps 1s/2s/4s before each of 3 retries on `genai_errors.APIError`, then raise the existing 502) and log per-call latency on success. All LLM paths route through `_generate_content`, so this covers opening/reply/nudge/grading/hint.

**Files:**
- Modify: `app/services/llm_gateway.py:62-68` (the `_generate_content` method) + module imports
- Test: `tests/test_llm_gateway.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_generate_content` unchanged signature; emits `{"event":"llm_latency",...}` on success and `{"event":"llm_retry",...}` on each retry; raises `HTTPException(502)` after exhausting retries (unchanged contract).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_llm_gateway.py` (check the file's existing imports; it already constructs `LLMGateway`). Append:

```python
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from google.genai import errors as genai_errors


def _api_error():
    # genai APIError needs a response_json; build a minimal one.
    return genai_errors.APIError(503, {"error": {"message": "unavailable"}})


async def test_generate_content_retries_then_succeeds(monkeypatch):
    from app.services.llm_gateway import LLMGateway

    gw = LLMGateway.__new__(LLMGateway)
    gw.client = MagicMock()
    gw.model = "gemini-2.5-flash"

    calls = {"n": 0}

    def _call(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _api_error()
        return MagicMock(text="ok")

    gw.client.models.generate_content = _call
    sleeps: list[float] = []

    async def _fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr("app.services.llm_gateway.asyncio.sleep", _fake_sleep)

    resp = await gw._generate_content(model=gw.model, contents=[], config=None)
    assert resp.text == "ok"
    assert sleeps == [1, 2]  # slept before retry #1 and #2; #3 succeeded


async def test_generate_content_raises_502_after_exhausting_retries(monkeypatch):
    from app.services.llm_gateway import LLMGateway

    gw = LLMGateway.__new__(LLMGateway)
    gw.client = MagicMock()
    gw.model = "gemini-2.5-flash"
    gw.client.models.generate_content = MagicMock(side_effect=_api_error())

    sleeps: list[float] = []

    async def _fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr("app.services.llm_gateway.asyncio.sleep", _fake_sleep)

    with pytest.raises(HTTPException) as exc:
        await gw._generate_content(model=gw.model, contents=[], config=None)
    assert exc.value.status_code == 502
    assert sleeps == [1, 2, 4]  # slept before each of 3 retries, then gave up


async def test_generate_content_logs_latency_on_success(monkeypatch):
    from app.services.llm_gateway import LLMGateway

    gw = LLMGateway.__new__(LLMGateway)
    gw.client = MagicMock()
    gw.model = "gemini-2.5-flash"
    gw.client.models.generate_content = MagicMock(return_value=MagicMock(text="ok"))

    with patch("app.services.llm_gateway.logger") as mock_log:
        await gw._generate_content(model=gw.model, contents=[], config=None)

    logged = [json.loads(c.args[0]) for c in mock_log.info.call_args_list]
    assert any(e["event"] == "llm_latency" and "llm_latency_ms" in e for e in logged)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_llm_gateway.py -k "retries or 502 or latency" -v`
Expected: FAIL (no retry/latency behavior yet; `asyncio.sleep` not patchable as attribute, latency not logged)

- [ ] **Step 3: Implement**

In `app/services/llm_gateway.py`, add imports near the top (after existing imports):

```python
import logging
import time

logger = logging.getLogger(__name__)
```

Replace `_generate_content`:

```python
    async def _generate_content(self, **kwargs):
        # Up to 3 retries with exponential backoff (1s, 2s, 4s) before each retry;
        # then surface a 502 just as before.
        delays = (1, 2, 4)
        attempt = 0
        while True:
            try:
                start = time.perf_counter()
                response = await asyncio.to_thread(
                    self.client.models.generate_content, **kwargs
                )
                logger.info(json.dumps({
                    "event": "llm_latency",
                    "model": kwargs.get("model", self.model),
                    "llm_latency_ms": round((time.perf_counter() - start) * 1000, 2),
                }))
                return response
            except genai_errors.APIError as exc:
                if attempt >= len(delays):
                    raise HTTPException(
                        status_code=502,
                        detail=f"LLM provider error: {exc.message or exc.status}",
                    ) from exc
                logger.warning(json.dumps({
                    "event": "llm_retry",
                    "attempt": attempt + 1,
                    "error": exc.message or str(exc.status),
                }))
                await asyncio.sleep(delays[attempt])
                attempt += 1
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_llm_gateway.py -v`
Expected: PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add app/services/llm_gateway.py tests/test_llm_gateway.py
git commit -m "feat: LLM gateway retry with backoff + per-call latency logging"
```

---

### Task 3: Bot-reply task — "respond shortly" push + generation latency log

On the **first** failed attempt of `generate_and_send_reply`, push a one-time "Your patient will respond shortly" notification before re-queuing. Also log the patient-reply generation time (the async leg not covered by request middleware).

**Files:**
- Modify: `app/tasks/bot_reply.py`
- Test: `tests/test_bot_reply.py`

**Interfaces:**
- Consumes: `app.tasks._run.run_task_async` (Task 1), `send_push.delay` (existing).
- Produces: `generate_and_send_reply` now sends a `{"type":"reply_delayed"}` push once on first failure; `_generate_and_send` emits `{"event":"bot_reply_latency","ms":...}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_bot_reply.py`:

```python
def test_generate_and_send_reply_pushes_once_on_first_failure():
    with patch("app.tasks.bot_reply.run_task_async") as mock_run, \
         patch("app.tasks.bot_reply._generate_and_send", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.bot_reply.send_push") as mock_push:
        mock_run.side_effect = Exception("LLM unavailable")

        from app.tasks.bot_reply import generate_and_send_reply
        from celery.exceptions import Retry

        with pytest.raises(Retry):
            generate_and_send_reply.apply(args=["session-id-str"], throw=True)

        mock_push.delay.assert_called_once()
        body = mock_push.delay.call_args.args[2]
        assert "shortly" in body.lower()


def test_generate_and_send_reply_no_push_on_later_retry():
    with patch("app.tasks.bot_reply.run_task_async") as mock_run, \
         patch("app.tasks.bot_reply._generate_and_send", MagicMock(return_value="coro-sentinel")), \
         patch("app.tasks.bot_reply.send_push") as mock_push:
        mock_run.side_effect = Exception("LLM unavailable")

        from app.tasks.bot_reply import generate_and_send_reply
        from celery.exceptions import Retry

        # request.retries == 1 simulates the second attempt.
        with pytest.raises(Retry):
            generate_and_send_reply.apply(
                args=["session-id-str"], throw=True, retries=1
            )

        mock_push.delay.assert_not_called()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_bot_reply.py -k "first_failure or later_retry" -v`
Expected: FAIL (no push on failure yet)

- [ ] **Step 3: Implement**

In `app/tasks/bot_reply.py`, change the entrypoint:

```python
@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def generate_and_send_reply(self, session_id: str) -> None:
    try:
        run_task_async(_generate_and_send(session_id, self.request.id))
    except Exception as exc:
        if self.request.retries == 0:
            try:
                user_id = run_task_async(_session_user_id(session_id))
                if user_id:
                    send_push.delay(
                        user_id,
                        "PocketPatient",
                        "Your patient will respond shortly",
                        {"type": "reply_delayed", "session_id": session_id},
                    )
            except Exception:
                logger.exception(
                    "generate_and_send_reply: delayed-reply push failed for session=%s",
                    session_id,
                )
        raise self.retry(exc=exc)
```

Add the helper above the task (uses its own session; small and read-only):

```python
async def _session_user_id(session_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                select(Session.user_id).where(Session.id == uuid.UUID(session_id))
            )
        ).scalar_one_or_none()
        return str(row) if row else None
```

Add the generation-latency log inside `_generate_and_send`: wrap the `generate_in_character(...)` call:

```python
        import time
        _t0 = time.perf_counter()
        reply_text = await generate_in_character(
            lambda: gateway.generate_patient_message(
                disease, patient_name, patient_age, history
            ),
            disease_name=disease.name,
            db=db,
            session_id=session.id,
        )
        logger.info(json.dumps({
            "event": "bot_reply_latency",
            "session_id": session_id,
            "ms": round((time.perf_counter() - _t0) * 1000, 2),
        }))
```

Add `import json` to the module imports if not present.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_bot_reply.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/tasks/bot_reply.py tests/test_bot_reply.py
git commit -m "feat: one-time delayed-reply push + bot-reply latency log"
```

---

### Task 4: Pragmatic dead-letter — LoggingTask base

A Celery base task whose `on_failure` (fired only after retries are exhausted) logs the dead task as a structured error. Wire it as the default task class so every task inherits it.

**Files:**
- Create: `app/tasks/base.py`
- Modify: `app/celery_app.py:8-17` (add `task_cls=...` to the `Celery(...)` call)
- Test: `tests/test_task_base.py`

**Interfaces:**
- Produces: `app.tasks.base.LoggingTask(celery.Task)` with `on_failure` logging `{"event":"dead-letter",...}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_task_base.py`:

```python
from __future__ import annotations

import json
from unittest.mock import patch


def test_logging_task_on_failure_logs_dead_letter():
    from app.tasks.base import LoggingTask

    task = LoggingTask()
    task.name = "app.tasks.demo"

    with patch("app.tasks.base.logger") as mock_log:
        task.on_failure(
            ValueError("boom"), "task-123", ("arg1",), {"k": "v"}, None
        )

    payload = json.loads(mock_log.error.call_args.args[0])
    assert payload["event"] == "dead-letter"
    assert payload["task"] == "app.tasks.demo"
    assert payload["task_id"] == "task-123"
    assert "boom" in payload["exc"]


def test_celery_uses_logging_task_base():
    from app.celery_app import celery
    from app.tasks.base import LoggingTask

    assert issubclass(celery.Task, LoggingTask)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_task_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.tasks.base'`

- [ ] **Step 3: Implement**

Create `app/tasks/base.py`:

```python
from __future__ import annotations

import json
import logging

from celery import Task

logger = logging.getLogger(__name__)


def _safe(value: object) -> str:
    try:
        return repr(value)
    except Exception:  # noqa: BLE001
        return "<unreprable>"


class LoggingTask(Task):
    """Default base task. on_failure fires only after retries are exhausted
    (Celery semantics), so this is the Redis-broker equivalent of a dead-letter
    queue: the finally-failed task is logged as a structured error for triage."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: ANN001
        logger.error(json.dumps({
            "event": "dead-letter",
            "task": self.name,
            "task_id": task_id,
            "args": _safe(args),
            "kwargs": _safe(kwargs),
            "exc": repr(exc),
        }))
        super().on_failure(exc, task_id, args, kwargs, einfo)
```

In `app/celery_app.py`, change the `Celery(...)` constructor to pass `task_cls`:

```python
celery = Celery(
    "pocket_patient",
    broker=settings.redis_url,
    task_cls="app.tasks.base:LoggingTask",
    include=[
        "app.tasks.bot_reply",
        "app.tasks.nudge",
        "app.tasks.case_initiation",
        "app.tasks.push_notifications",
    ],
)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_task_base.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite (task_cls change is global)**

Run: `uv run pytest -v`
Expected: PASS (all tasks still register and behave as before)

- [ ] **Step 6: Commit**

```bash
git add app/tasks/base.py app/celery_app.py tests/test_task_base.py
git commit -m "feat: LoggingTask base logs dead-letter on final task failure"
```

---

### Task 5: FCM stale-token handling

In `send_push`, a dead token (`UnregisteredError` / `SenderIdMismatchError`) clears the user's `fcm_token` and returns without retry; any other error still retries.

**Files:**
- Modify: `app/tasks/push_notifications.py`
- Test: `tests/test_push_task.py`

**Interfaces:**
- Consumes: `run_task_async` (Task 1).
- Produces: `_clear_fcm_token(user_id: str) -> None` (async helper); `send_push` clears token + logs `{"event":"fcm-token-stale",...}` on dead-token errors.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_push_task.py`:

```python
def test_send_push_clears_token_and_does_not_retry_on_unregistered():
    from firebase_admin import messaging

    with patch("app.tasks.push_notifications.run_task_async") as mock_run, \
         patch("app.tasks.push_notifications.push_service") as mock_ps, \
         patch("app.tasks.push_notifications._clear_fcm_token",
               MagicMock(return_value="clear-coro")):
        # 1st run_task_async -> token lookup; 2nd -> clear-token coroutine.
        mock_run.side_effect = ["device-token", None]
        mock_ps.send_push_notification.side_effect = messaging.UnregisteredError("gone")

        from app.tasks.push_notifications import _clear_fcm_token, send_push

        # No Retry raised — returns normally.
        send_push.apply(args=["user-id", "t", "b", {}], throw=True)

        _clear_fcm_token.assert_called_once_with("user-id")


def test_send_push_retries_on_generic_error():
    with patch("app.tasks.push_notifications.run_task_async") as mock_run, \
         patch("app.tasks.push_notifications.push_service") as mock_ps:
        mock_run.return_value = "token"
        mock_ps.send_push_notification.side_effect = Exception("transient FCM")

        from app.tasks.push_notifications import send_push
        from celery.exceptions import Retry

        with pytest.raises(Retry):
            send_push.apply(args=["user-id", "t", "b", {}], throw=True)
```

Note: update the existing `test_send_push_retries_on_exception` if it overlaps — keep it (generic `Exception` still retries).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_push_task.py -k "unregistered or generic_error" -v`
Expected: FAIL (`_clear_fcm_token` does not exist; UnregisteredError currently hits the generic retry)

- [ ] **Step 3: Implement**

Rewrite `app/tasks/push_notifications.py`:

```python
from __future__ import annotations

import json
import logging
import uuid

from firebase_admin import messaging
from sqlalchemy import select, update

from app.celery_app import celery
from app.database import AsyncSessionLocal
from app.models.user import User
from app.services import push_service
from app.tasks._run import run_task_async

logger = logging.getLogger(__name__)


async def _get_fcm_token(user_id: str) -> str | None:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User.fcm_token).where(User.id == uid))
        return result.scalar_one_or_none()


async def _clear_fcm_token(user_id: str) -> None:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == uid).values(fcm_token=None))
        await db.commit()


@celery.task(bind=True, max_retries=3, default_retry_delay=60)
def send_push(self, user_id, title, body, data):
    token = run_task_async(_get_fcm_token(user_id))
    if not token:
        return
    try:
        push_service.send_push_notification(token, title, body, data)
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
        # Token is dead — retrying is pointless. Clear it; the client re-registers
        # via PUT /users/me/fcm-token on next app open.
        run_task_async(_clear_fcm_token(user_id))
        logger.warning(json.dumps({"event": "fcm-token-stale", "user_id": user_id}))
        return
    except Exception as exc:
        raise self.retry(exc=exc)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_push_task.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/tasks/push_notifications.py tests/test_push_task.py
git commit -m "feat: clear stale FCM token on UnregisteredError without retry"
```

---

### Task 6: Slow-query observability listener

Register SQLAlchemy cursor-execute listeners that log queries slower than a threshold, giving the "identify slow queries" signal.

**Files:**
- Create: `app/observability.py`
- Modify: `app/main.py` (call the registrar at import/startup)
- Test: `tests/test_observability.py`

**Interfaces:**
- Produces: `app.observability.register_slow_query_logging(engine, threshold_ms: float = 50.0) -> None` — attaches `before/after_cursor_execute` listeners that `logger.warning({"event":"slow_query",...})` when elapsed > threshold.

- [ ] **Step 1: Write the failing test**

Create `tests/test_observability.py`:

```python
from __future__ import annotations

import json
from unittest.mock import patch

from sqlalchemy import create_engine, text


def test_slow_query_logged_above_threshold():
    from app.observability import register_slow_query_logging

    engine = create_engine("sqlite://")
    register_slow_query_logging(engine, threshold_ms=0.0)  # everything is "slow"

    with patch("app.observability.logger") as mock_log:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    events = [json.loads(c.args[0])["event"] for c in mock_log.warning.call_args_list]
    assert "slow_query" in events


def test_fast_query_not_logged_below_threshold():
    from app.observability import register_slow_query_logging

    engine = create_engine("sqlite://")
    register_slow_query_logging(engine, threshold_ms=10_000.0)  # nothing is "slow"

    with patch("app.observability.logger") as mock_log:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    assert mock_log.warning.call_count == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_observability.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.observability'`

- [ ] **Step 3: Implement**

Create `app/observability.py`:

```python
from __future__ import annotations

import json
import logging
import time

from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_START_KEY = "_pp_query_start"


def register_slow_query_logging(engine: Engine, threshold_ms: float = 50.0) -> None:
    """Log any SQL statement whose execution exceeds threshold_ms. `engine` is the
    sync Engine (for an async engine, pass engine.sync_engine)."""

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info[_START_KEY] = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        start = conn.info.pop(_START_KEY, None)
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms > threshold_ms:
            logger.warning(json.dumps({
                "event": "slow_query",
                "duration_ms": round(elapsed_ms, 2),
                "statement": " ".join(statement.split())[:300],
            }))
```

In `app/main.py`, register against the app engine's sync engine. Add near where the app/engine is set up (after `from app.database import engine` — add the import if absent):

```python
from app.database import engine
from app.observability import register_slow_query_logging

register_slow_query_logging(engine.sync_engine)
```

(Place this at module level in `app/main.py`, after imports, so it runs once on app import.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_observability.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite (main.py import side effect)**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/observability.py app/main.py tests/test_observability.py
git commit -m "feat: slow-query logging listener on the app engine"
```

---

### Task 7: Index on messages(session_id, sent_at)

Postgres does not auto-index the `messages.session_id` FK; a composite `(session_id, sent_at)` index serves transcript ordering and the latest-message lookups.

**Files:**
- Create: `alembic/versions/<generated>_add_messages_session_id_sent_at_index.py`

**Interfaces:** none (schema change).

- [ ] **Step 1: Generate the migration skeleton**

Run: `uv run alembic revision -m "add messages session_id sent_at index"`
Expected: creates a new file under `alembic/versions/` with `down_revision = "4522b2be2830"` prefilled.

- [ ] **Step 2: Fill in upgrade/downgrade**

Edit the generated file's `upgrade()` / `downgrade()`:

```python
def upgrade() -> None:
    op.create_index(
        "ix_messages_session_id_sent_at",
        "messages",
        ["session_id", "sent_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_session_id_sent_at", table_name="messages")
```

- [ ] **Step 3: Apply and verify round-trip**

Run:
```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```
Expected: all succeed; no errors. (Index created, dropped, recreated.)

- [ ] **Step 4: Add a matching Index to the model (keep model/DB in sync)**

In `app/models/message.py`, add `__table_args__` so the test DB (built from `Base.metadata`) also has the index:

```python
from sqlalchemy import Index
# ...
class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_session_id_sent_at", "session_id", "sent_at"),
    )
```

- [ ] **Step 5: Run full suite (tests build schema from metadata)**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/ app/models/message.py
git commit -m "perf: index messages(session_id, sent_at)"
```

---

### Task 8: End-to-end simulation test suite

The headline deliverable: a deterministic, offline suite exercising the full case lifecycle with a mocked gateway, eager Celery, and a per-task test-DB engine factory (so the thread-spawned task loops from Task 1 bind their own connections).

**Files:**
- Create: `tests/test_e2e_simulation.py`

**Interfaces:**
- Consumes: `run_task_async` (Task 1), the eager Celery config, all task entrypoints, the `professor`/`student`/`db_session`/`clean_tables`/`client` fixtures.

- [ ] **Step 1: Write the harness + lifecycle test**

Create `tests/test_e2e_simulation.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.course import Course
from app.models.disease import Disease
from app.models.enrollment import Enrollment
from app.models.message import Message, MessageRole
from app.models.session import Session, SessionStatus
from app.models.unit import Unit, UnitStatus
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

_PRESSURED_NUDGE = {"frequency": "high", "tone": "urgent", "example": "please!"}
_FLAT_NUDGE = {"frequency": "low", "tone": "withdrawn", "example": "..."}


# --- a per-call test-DB session whose engine is built inside the calling loop ---
# Task entrypoints run on a fresh thread/loop (run_task_async); an async engine's
# connections are bound to the loop that created them, so each task gets its own
# engine created inside its own loop, disposed on context exit.
class _PerCallTestSession:
    def __call__(self):
        engine = create_async_engine(TEST_DATABASE_URL)
        session = async_sessionmaker(engine, expire_on_commit=False)()
        return _SessionCtx(engine, session)


class _SessionCtx:
    def __init__(self, engine, session):
        self._engine = engine
        self._session = session

    async def __aenter__(self):
        return await self._session.__aenter__()

    async def __aexit__(self, *exc):
        result = await self._session.__aexit__(*exc)
        await self._engine.dispose()
        return result


@pytest.fixture
def e2e_harness(monkeypatch):
    """Eager Celery + mocked gateway + recorded push + per-task test-DB sessions."""
    from app.celery_app import celery

    monkeypatch.setattr(celery.conf, "task_always_eager", True, raising=False)
    monkeypatch.setattr(celery.conf, "task_eager_propagates", True, raising=False)

    # Route every task's AsyncSessionLocal at the test DB (loop-safe per call).
    factory = _PerCallTestSession()
    for mod in (
        "app.tasks.bot_reply",
        "app.tasks.nudge",
        "app.tasks.case_initiation",
        "app.tasks.push_notifications",
    ):
        monkeypatch.setattr(f"{mod}.AsyncSessionLocal", factory)
    # create_new_session receives db from the caller, but session_service also
    # imports symbols; its create path uses the passed db, so no patch needed.

    # Record FCM sends instead of hitting firebase.
    pushes: list[tuple] = []
    monkeypatch.setattr(
        "app.services.push_service.send_push_notification",
        lambda token, title, body, data: pushes.append((token, title, body, data)),
    )

    # Mock the gateway. Canned text must NOT contain the disease name (would trip
    # the diagnosis-leak guardrail) or AI-break phrases. Record calls for assertions.
    calls: dict[str, list] = {"opening": [], "patient": [], "nudge": [], "grade": [], "hint": []}

    async def _opening(disease, name, age):
        calls["opening"].append(disease)
        return "Hello doctor, I have not been feeling like myself lately."

    async def _patient(disease, name, age, history):
        calls["patient"].append(disease)
        return "It has been hard to get through the day."

    async def _nudge(disease, name, age, hours):
        calls["nudge"].append((disease, hours))
        return "Are you still there, doctor?"

    grade_result = {"is_correct": False, "rubric_score": 40.0, "feedback": "keep going"}

    async def _grade(disease, submission, transcript):
        calls["grade"].append(disease)
        return dict(grade_result)

    async def _hint(wrong_dx, actual_dx):
        calls["hint"].append((wrong_dx, actual_dx))
        return "Consider the mood symptoms more closely."

    monkeypatch.setattr("app.services.llm_gateway.gateway.generate_opening_message", _opening)
    monkeypatch.setattr("app.services.llm_gateway.gateway.generate_patient_message", _patient)
    monkeypatch.setattr("app.services.llm_gateway.gateway.generate_nudge_message", _nudge)
    monkeypatch.setattr("app.services.llm_gateway.gateway.grade_diagnosis", _grade)
    monkeypatch.setattr("app.services.llm_gateway.gateway.generate_hint", _hint)

    return {"pushes": pushes, "calls": calls, "grade_result": grade_result}


async def _seed(db_session, prof, stu, *, speech_style, nudge_behavior, name):
    course = Course(
        title="E2E", professor_id=prof.id, class_code=f"E2E{uuid.uuid4().hex[:3].upper()}",
        is_active=True, msg_window_start=time(0, 0), msg_window_end=time(23, 59),
        msg_timezone="UTC",
    )
    db_session.add(course)
    await db_session.flush()
    unit = Unit(course_id=course.id, label="U1", status=UnitStatus.released,
                release_date=datetime.now(timezone.utc))
    db_session.add(unit)
    await db_session.flush()
    disease = Disease(
        unit_id=unit.id, name=name, category="Mood", key_symptoms=["low mood"],
        differentials=["GAD"], difficulty_tier=2, speech_style=speech_style,
        nudge_behavior=nudge_behavior,
    )
    db_session.add(disease)
    db_session.add(Enrollment(user_id=stu.id, course_id=course.id))
    stu.fcm_token = "device-token"
    db_session.add(stu)
    await db_session.commit()
    await db_session.refresh(course)
    await db_session.refresh(disease)
    return course, disease


async def test_full_case_lifecycle(e2e_harness, professor, student, db_session, client):
    from app.tasks.case_initiation import initiate_case

    prof, _ = professor
    stu, token = student
    course, disease = await _seed(
        db_session, prof, stu, speech_style="flat",
        nudge_behavior=_FLAT_NUDGE, name="Major Depressive Disorder",
    )
    auth = {"Authorization": f"Bearer {token}"}

    # 1. Scheduler initiates the case (eager).
    initiate_case(str(stu.id), str(course.id))
    sess = (await db_session.execute(
        select(Session).where(Session.user_id == stu.id, Session.status == SessionStatus.active)
    )).scalar_one()
    msgs = (await db_session.execute(
        select(Message).where(Message.session_id == sess.id)
    )).scalars().all()
    assert any(m.role == MessageRole.patient for m in msgs)  # opening message
    assert any(p[3]["type"] == "new_case" for p in e2e_harness["pushes"])

    # 2-4. Student replies → eager bot reply persists a patient turn.
    for _ in range(2):
        r = await client.post(
            f"/api/v1/sessions/{sess.id}/messages?instant=true",
            json={"content": "Tell me more about how you feel."}, headers=auth,
        )
        assert r.status_code == 202
    await db_session.refresh(sess)
    patient_turns = (await db_session.execute(
        select(Message).where(
            Message.session_id == sess.id, Message.role == MessageRole.patient,
            Message.is_nudge == False,  # noqa: E712
        )
    )).scalars().all()
    assert len(patient_turns) >= 3  # opening + 2 replies
    assert sess.pending_reply_task_id is None  # cleared after each reply

    # 5. Wrong diagnosis → hint, session stays active.
    r = await client.post(
        f"/api/v1/sessions/{sess.id}/diagnose",
        json={"primary_dx": "Bipolar", "differentials": [], "justification": "guess"},
        headers=auth,
    )
    assert r.status_code == 200 and r.json()["correct"] is False
    assert r.json()["hint"]
    await db_session.refresh(sess)
    assert sess.status == SessionStatus.active

    # 6. Correct diagnosis → score + reveal, session diagnosed.
    e2e_harness["grade_result"]["is_correct"] = True
    r = await client.post(
        f"/api/v1/sessions/{sess.id}/diagnose",
        json={"primary_dx": "MDD", "differentials": [], "justification": "low mood"},
        headers=auth,
    )
    body = r.json()
    assert r.status_code == 200 and body["correct"] is True
    assert body["score"] is not None and body["reveal"]["disease_name"] == disease.name
    await db_session.refresh(sess)
    assert sess.status == SessionStatus.diagnosed

    # 7. Next case auto-initiates: student now has no active session.
    active = (await db_session.execute(
        select(Session).where(Session.user_id == stu.id, Session.status == SessionStatus.active)
    )).scalar_one_or_none()
    assert active is None
    initiate_case(str(stu.id), str(course.id))
    new_active = (await db_session.execute(
        select(Session).where(Session.user_id == stu.id, Session.status == SessionStatus.active)
    )).scalar_one()
    assert new_active.id != sess.id
```

- [ ] **Step 2: Run the lifecycle test**

Run: `uv run pytest tests/test_e2e_simulation.py::test_full_case_lifecycle -v`
Expected: PASS

- [ ] **Step 3: Add the speech-style + prompt-wiring test**

Append to `tests/test_e2e_simulation.py`:

```python
def test_speech_style_delay_ranges_and_prompt_wiring():
    from app.services.llm_gateway import LLMGateway
    from app.services.session_service import _reply_delay_seconds, _DELAY_RANGES_SEC
    from app.tasks.nudge import _FREQUENCY_HOURS

    # Deterministic, style-dependent reply delays.
    assert _reply_delay_seconds("pressured") == 0
    lo, hi = _DELAY_RANGES_SEC["flat"]
    d = _reply_delay_seconds("flat")
    assert lo <= d <= hi and (lo, hi) == (3600, 4 * 3600)

    # Nudge cadence differs per frequency tier.
    assert _FREQUENCY_HOURS["high"] != _FREQUENCY_HOURS["low"]

    # Prompt wiring: speech_style flows into the system prompt.
    gw = LLMGateway.__new__(LLMGateway)
    disease = MagicMock(name="d", speech_style="pressured", dsm_code="F00",
                        key_symptoms=["x"])
    disease.name = "Schizophrenia"
    prompt = gw._build_system_prompt(disease, "Sarah", 40)
    assert "pressured" in prompt
```

- [ ] **Step 4: Add the nudge-after-silence test**

Append:

```python
async def test_nudge_after_24h_silence(e2e_harness, professor, student, db_session):
    from app.tasks.nudge import check_and_send_nudges

    prof, _ = professor
    stu, _ = student
    course, disease = await _seed(
        db_session, prof, stu, speech_style="flat",
        nudge_behavior=_FLAT_NUDGE, name="Major Depressive Disorder",
    )
    sess = Session(
        disease_id=disease.id, user_id=stu.id, course_id=course.id,
        started_at=datetime.now(timezone.utc), status=SessionStatus.active, turn_count=1,
    )
    db_session.add(sess)
    await db_session.flush()
    # Latest message is a patient message 25h old → eligible for first nudge.
    db_session.add(Message(
        session_id=sess.id, role=MessageRole.patient, content="hello",
        sent_at=datetime.now(timezone.utc) - timedelta(hours=25), is_nudge=False,
    ))
    await db_session.commit()

    check_and_send_nudges()  # eager

    nudges = (await db_session.execute(
        select(Message).where(Message.session_id == sess.id, Message.is_nudge == True)  # noqa: E712
    )).scalars().all()
    assert len(nudges) == 1
    assert e2e_harness["calls"]["nudge"], "nudge generation was invoked"
    called_disease, _hours = e2e_harness["calls"]["nudge"][0]
    assert called_disease.nudge_behavior["tone"] == _FLAT_NUDGE["tone"]
    assert any(p[3]["type"] == "new_message" for p in e2e_harness["pushes"])
```

- [ ] **Step 5: Run the whole e2e file**

Run: `uv run pytest tests/test_e2e_simulation.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (entire suite)

- [ ] **Step 7: Commit**

```bash
git add tests/test_e2e_simulation.py
git commit -m "test: end-to-end case-lifecycle simulation suite"
```

---

## Verification notes (performance baseline)

After Tasks 2/3/6, exercise the dev system and read the logs to record the baseline:
- `event=llm_latency` → compute p50/p95 of `llm_latency_ms` from logs. Note: not streamed, so this is full-call latency; "first token < 1.5s" cannot be isolated without streaming (out of scope — documented in the spec).
- `event=bot_reply_latency` → async reply-generation leg; combine with the request-middleware `duration_ms` for student POST to characterize round-trip.
- `event=slow_query` → any statement > 50 ms; confirm none recur for session/message reads after the Task 7 index.

These numbers are recorded by hand (not asserted); no concurrency load harness this week (per design decision).

## Self-Review

- **Spec coverage:** Supporting runner → Task 1. T1 e2e suite → Task 8. T2a LLM retry → Task 2; T2b respond-shortly push → Task 3; T2c LoggingTask DLQ → Task 4; T2d FCM stale token → Task 5. T3a LLM latency → Task 2; T3b slow-query listener → Task 6; T3c index → Task 7; T3d bot-reply latency → Task 3. All spec sections mapped.
- **Placeholders:** none — every code/test step has full content; the only "generated" filename is the Alembic revision, created by the `alembic revision` command in Task 7 Step 1.
- **Type consistency:** `run_task_async(coro)` defined in Task 1, consumed by Tasks 3/5/8. `_clear_fcm_token(user_id)`/`_session_user_id(session_id)` defined where used. `register_slow_query_logging(engine, threshold_ms)` defined in Task 6, called in `main.py`. `LoggingTask` defined in Task 4, referenced by `celery_app` + test. Gateway/session_service constants (`_DELAY_RANGES_SEC`, `_reply_delay_seconds`, `_FREQUENCY_HOURS`) referenced in Task 8 exist in the current codebase (verified).
