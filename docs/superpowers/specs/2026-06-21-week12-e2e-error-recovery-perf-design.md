# Week 12 Backend — E2E Simulation, Error Recovery, Performance Baseline

**Date:** 2026-06-21
**Phase:** 2 (Core Simulation) wrap-up / exit
**Source:** `week-12.md` — Dev A (Backend) Tasks 1–3

## Goal

Close out Phase 2 by (1) proving the full case lifecycle end-to-end with a deterministic, offline integration test suite, (2) hardening the system against LLM, Celery, and FCM failures, and (3) instrumenting latency and adding indexes for a performance baseline.

No new product features — this is verification + resilience + observability over the existing simulation pipeline.

## Existing lifecycle (context)

```
beat: check_and_initiate_cases ──(eta, dedup)──▶ initiate_case
   └─ create_new_session ─▶ opening message (gateway.generate_opening_message via guardrail) + send_push("new_case")
student POST /sessions/{id}/messages
   └─ handle_student_message: persist student msg, refresh avg latency,
      revoke prior pending reply, schedule generate_and_send_reply(eta = speech_style delay)
generate_and_send_reply (Celery)
   └─ _generate_and_send: guard (active + task_id matches), gateway.generate_patient_message,
      persist patient msg, turn_count++, clear pending_reply_task_id, send_push("new_message")
POST /sessions/{id}/diagnose
   └─ wrong: grade → generate_diagnosis_hint → {correct:false, hint}, session stays active
   └─ correct: persist Score, status=diagnosed, revoke pending reply → {correct:true, score, reveal}
beat: check_and_send_nudges ─▶ _maybe_send_nudge per silent active session (cadence by nudge_behavior.frequency)
```

LLM calls funnel through `LLMGateway._generate_content`. Push funnels through the `send_push` Celery task → `push_service.send_push_notification`. Next-case auto-initiation is **implicit**: once a session is `diagnosed`, the student has no active session and the 15-min scheduler re-initiates.

## Supporting change — loop-safe task runner

Every Celery task entrypoint currently calls `asyncio.run(...)`. Under `task_always_eager` (Task 1), the task body executes inside the test's already-running event loop, so `asyncio.run` raises `RuntimeError: asyncio.run() cannot be called from a running event loop`.

**Change:** add `app/tasks/_run.py`:

```python
def run_task_async(coro) -> T:
    """Run `coro` to completion on a dedicated thread with its own event loop,
    propagating its return value or exception. Loop-safe whether or not an event
    loop is already running in the calling thread (needed for Celery eager mode
    in tests; harmless in the prefork worker, where each task still gets a fresh
    loop so the per-task pool reset in celery_app still applies)."""
```

Refactor the four entrypoints to use it:
- `app/tasks/bot_reply.py::generate_and_send_reply`
- `app/tasks/nudge.py::check_and_send_nudges`
- `app/tasks/case_initiation.py::check_and_initiate_cases` and `initiate_case`
- `app/tasks/push_notifications.py::send_push`

Behavior in the real worker is unchanged (each task gets its own loop; `_reset_db_engine_pool` still rebinds connections). It simply no longer assumes the absence of a running loop.

## Task 1 — End-to-end simulation test suite

New file `tests/test_e2e_simulation.py`. Deterministic and fully offline.

### Harness (fixtures)

- **Eager Celery:** module-scoped autouse fixture sets `celery.conf.task_always_eager = True` and `task_eager_propagates = True`; restores both on teardown.
- **Gateway fakes:** monkeypatch `app.services.llm_gateway.gateway` methods (`generate_opening_message`, `generate_patient_message`, `generate_nudge_message`, `grade_diagnosis`, `generate_hint`) with canned, **call-recording** fakes (record args, esp. the `Disease` passed and `nudge_behavior`). Default grading fake is controllable per-test (wrong vs. correct).
- **No real FCM:** patch `app.services.push_service.send_push_notification` to a recording no-op.
- **Seed helper:** builds a course with a released unit and two diseases with distinct `speech_style` (`pressured` → delay 0; `flat` → delay 3600–14400s) and distinct `nudge_behavior.frequency`, plus an enrolled student. Reuses existing `conftest` fixtures where possible.

### Tests

1. **Full lifecycle (`test_full_case_lifecycle`)**
   - `initiate_case` (eager) → asserts an active session, one persisted opening (`role=patient`) message, and a recorded `new_case` push.
   - Student POSTs a reply (`POST /sessions/{id}/messages`) → eager `generate_and_send_reply` runs → asserts a persisted patient reply, `turn_count` incremented, `pending_reply_task_id` cleared, and a `new_message` push.
   - Repeat for ≥2 rounds.
   - Wrong diagnosis (grading fake returns `is_correct=False`) → `{correct:false, hint}`, session still `active`, no `Score` row, hint fake invoked.
   - Correct diagnosis → `{correct:true, score, reveal}`, session `diagnosed`, `Score` row present, pending reply revoked.
   - **Next-case auto-init:** assert the student now has no active session, then `initiate_case` again creates a fresh active session with a new opening message (proves the implicit re-initiation path).

2. **Speech-style differentiation (`test_speech_style_patterns`)**
   - Capture `generate_and_send_reply.apply_async` kwargs (spy/wrapper) for a `pressured` vs. a `flat` disease; assert the scheduled delay is `0` for `pressured` and within `[3600, 14400]` for `flat` (derived from the `eta`).
   - **Prompt wiring:** assert `LLMGateway._build_system_prompt(disease, name, age)` contains `disease.speech_style`, and that the recorded `generate_patient_message` call received the matching `Disease`.
   - Nudge cadence: assert `_FREQUENCY_HOURS` maps each disease's `nudge_behavior.frequency` to a distinct gap.

3. **Nudge (`test_nudge_after_silence`)**
   - Seed an active session whose latest message is a patient message dated ≥24h ago.
   - Run `check_and_send_nudges` (eager) → assert exactly one new `is_nudge=True` patient message persisted, a `new_message` push recorded, and `generate_nudge_message` invoked with the disease's `nudge_behavior` tone.

## Task 2 — Error recovery

### 2a. LLM retry with exponential backoff

In `LLMGateway._generate_content`, wrap the call in a 3-attempt loop. On `genai_errors.APIError`, `await asyncio.sleep(d)` with `d ∈ {1, 2, 4}` between attempts (no sleep after the final attempt), structured-log each retry, and on exhaustion raise the existing `HTTPException(502)`. Empty-response and other current behavior unchanged. Covers every LLM path (opening, patient reply, nudge, grading, hint) since all route through `_generate_content`.

### 2b. Re-queue + "respond shortly" push

`generate_and_send_reply` already `self.retry(exc=exc)` (`max_retries=3`, `default_retry_delay=60`). Add: when the body raises **and this is the first attempt** (`self.request.retries == 0`), dispatch a one-time `send_push.delay(user_id, "PocketPatient", "Your patient will respond shortly", {...})` before re-raising via `self.retry`. The student is told once that a reply is delayed, not on every retry. Look up `user_id` defensively (the session row) and swallow push-dispatch errors.

### 2c. Pragmatic dead-letter (Redis broker)

Add `app/tasks/base.py`:

```python
class LoggingTask(celery.Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(json.dumps({
            "event": "dead-letter", "task": self.name, "task_id": task_id,
            "args": _safe(args), "exc": repr(exc),
        }))
```

Wire as the default task base via `Celery(..., task_cls="app.tasks.base:LoggingTask")` in `celery_app.py` so **all** tasks inherit on-failure logging. `on_failure` fires only after retries are exhausted (Celery semantics), giving "retried up to 3 times, then logged as error." `max_retries=3` stays.

### 2d. FCM stale-token handling

In `send_push`, distinguish a dead token from a transient failure:
- Catch `firebase_admin.messaging.UnregisteredError` (and token-invalid `messaging.SenderIdMismatchError`) → null the user's `fcm_token` (async helper `_clear_fcm_token(user_id)`), log `event=fcm-token-stale`, and **return without retry** (retrying a dead token is pointless).
- Any other exception → `self.retry(exc=exc)` as today.

Re-registration needs no backend change: the existing `PUT /users/me/fcm-token` upserts the token on next app open. Documented here.

## Task 3 — Performance baseline

Instrumentation + indexes only — no concurrency load harness.

### 3a. LLM latency logging

In `_generate_content`, measure wall time with `time.perf_counter()` around the call and log `{"event":"llm_latency","llm_latency_ms":..,"model":..}` (on success; failures already logged in 2a). **Known limitation:** responses are not streamed, so this is full-call latency; the `week-12.md` "first token < 1.5s" target cannot be isolated without streaming, which is out of scope this week. Recorded as a caveat in the baseline notes.

### 3b. DB query timing / slow-query identification

Add `app/observability.py` registering SQLAlchemy `before_cursor_execute` / `after_cursor_execute` listeners on the engine. Stash a start time on the connection, compute elapsed ms, and `logger.warning({"event":"slow_query","duration_ms":..,"statement":..})` when elapsed exceeds a threshold (default 50 ms; configurable). Register at app startup (`app/main.py`). This is the mechanism for "identify slow queries."

### 3c. Indexes

Audit (current): `messages` has **no index on `session_id`** (Postgres does not auto-index FKs); `sessions` already has the partial unique index `uq_one_active_session_per_user_course (user_id, course_id) WHERE status='active'` which covers active-session lookups.

Alembic migration adds:
- `ix_messages_session_id_sent_at` on `messages (session_id, sent_at)` — serves `get_session_messages` (ORDER BY sent_at), the nudge latest-message lookups, and `last_patient_msg`.

No `sessions` index added (the partial unique index already covers the hot path). Migration created by hand (not autogenerate) to control naming; verified with `alembic upgrade head` + `downgrade`.

### 3d. Round-trip visibility

`LoggingMiddleware` already logs request `duration_ms`. Add a generation-time log inside `generate_and_send_reply` (`{"event":"bot_reply_latency","ms":..}`) so the async reply leg — the part not covered by request middleware — is measurable end-to-end.

## Out of scope

- Streaming / first-token measurement.
- A concurrency load-test harness for 10 sessions (instrumentation only, per decision).
- Persisted dead-letter table / replay tooling (log-only DLQ).
- `fcm_token_stale` boolean column (token is nulled instead).
- Professor transcript viewer / home-screen tabs (Dev B / frontend).

## Testing strategy

- Task 1: the new `tests/test_e2e_simulation.py` is itself the deliverable.
- Task 2: unit tests — gateway retries 3× then raises 502 (assert sleep schedule via patched `asyncio.sleep`); `generate_and_send_reply` sends the "respond shortly" push exactly once (retries==0); `LoggingTask.on_failure` logs dead-letter; `send_push` clears token on `UnregisteredError` and does not retry, retries on other errors.
- Task 3: unit tests — `_generate_content` logs `llm_latency`; slow-query listener logs above threshold and is silent below; migration up/down round-trips. Perf numbers captured by manually exercising the system and reading logs (recorded in the plan's verification notes, not asserted).
- Full suite green: `uv run pytest -v`.
