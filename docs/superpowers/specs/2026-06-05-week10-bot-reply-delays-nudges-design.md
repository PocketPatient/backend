# Week 10 Backend Design — Bot Reply Delays, Pathological Nudges, Latency Tracking

**Date:** 2026-06-05
**Phase:** 2 — Core Simulation
**Theme:** Simulated bot reply delays + pathological nudge system + response latency tracking.

---

## Goals

1. Replace the synchronous bot-reply path with a delayed Celery task whose ETA is driven by the disease's `speech_style`; the endpoint returns `202 Accepted` immediately.
2. Implement `check_and_send_nudges` (currently a stub from Week 9): detect students who've gone silent on an active session and send LLM-generated, in-character follow-up messages at a per-disease cadence.
3. Track `response_latency_sec` per student message (already implemented) and refresh `session.avg_response_latency_sec` after **every** student message (currently only refreshed at diagnosis time — moves earlier).

## Non-goals

- Frontend "typing..." / pull-to-refresh / response-time-dot UI (Dev B scope, week-10.md L56-75).
- Changing the push notification copy/data shape established in Week 9.
- Retry/DLQ tuning beyond what `send_push` and `generate_and_send_reply` need for their own LLM/Firebase calls.
- New analytics dashboards consuming `avg_response_latency_sec` (Phase 3, per week-10.md L50).

---

## Architecture

### New files

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `app/tasks/bot_reply.py` | `generate_and_send_reply` Celery task (delayed LLM reply + push) |
| Create | `alembic/versions/*_add_pending_reply_task_id_to_sessions.py` | Migration: nullable `pending_reply_task_id` on `sessions` |

### Modified files

| Action | Path | Change |
|--------|------|--------|
| Modify | `app/models/session.py` | Add `pending_reply_task_id: Mapped[str \| None]` |
| Modify | `app/services/session_service.py` | Split `send_student_message_and_get_reply`: student-message handling (latency, avg latency, schedule/cancel reply task) stays here as `handle_student_message`; reply-generation logic (LLM call, save patient message, push) moves to `app/tasks/bot_reply.py`. Add `_reply_delay_seconds` helper. |
| Modify | `app/routers/sessions.py` | `POST /sessions/{id}/messages` accepts `?instant=bool` query param, returns `202` + echoed student `MessageOut` |
| Modify | `app/tasks/nudge.py` | Replace the logging stub with the full `check_and_send_nudges` implementation |
| Modify | `app/services/llm_gateway.py` | Add `generate_nudge_message(disease, patient_name, patient_age, hours_since_last_message)` |
| Modify | `app/services/grading_service.py` | Drop its private `_avg_student_latency`; import the relocated helper from `session_service` instead |

### Data flows

**Student sends a message (HTTP path):**
```
POST /sessions/{id}/messages?instant=<bool>
  → session_service.handle_student_message(session, content, instant, db)
    1. Compute response_latency_sec from the prior patient message's sent_at
    2. Save Message(role=student, response_latency_sec=...)
    3. Recompute session.avg_response_latency_sec = mean(student response_latency_sec values)
    4. Best-effort revoke: celery.control.revoke(session.pending_reply_task_id) if set
    5. task_id = str(uuid4()); session.pending_reply_task_id = task_id
    6. db.commit()
    7. dispatch generate_and_send_reply:
         instant=True  → apply_async(args=[session_id], task_id=task_id)               # fires ASAP
         instant=False → apply_async(args=[session_id], task_id=task_id, eta=now+delay)  # delay from speech_style
    8. return the saved student Message
  → router returns 202 + MessageOut(student message)
```

**Delayed reply fires (Celery task):**
```
generate_and_send_reply(self, session_id)   # bind=True
  → _generate_and_send(session_id, self.request.id)
    1. open AsyncSessionLocal; load session
    2. if session.pending_reply_task_id != my_task_id: return   # superseded by a newer message — skip silently
    3. load disease + full transcript (logic moved from current send_student_message_and_get_reply)
    4. reply_text = await gateway.generate_patient_message(...)
    5. save Message(role=patient, is_nudge=False, sent_at=now)
    6. session.turn_count += 1
    7. UPDATE sessions SET pending_reply_task_id = NULL
         WHERE id = :id AND pending_reply_task_id = :my_task_id   # conditional clear — don't clobber a newer id
    8. commit
    9. send_push.delay(user_id, "PocketPatient", "Your patient replied",
                       {"type": "new_message", "session_id": str(session_id)})
```

**Nudge check (hourly Celery Beat task, schedule already wired up in Week 9):**
```
check_and_send_nudges()
  → _run_nudge_check()
    1. open AsyncSessionLocal
    2. find active sessions whose most-recent message (by sent_at) has role == patient
       AND that message's sent_at <= now - 24h   (latest-message-per-session subquery,
       avoids loading full transcripts for every active session)
    3. for each eligible session:
       a. frequency_hours = {"high": 6, "medium": 24, "low": 48}.get(disease.nudge_behavior["frequency"], 24)
       b. last_nudge = most recent Message with is_nudge=True for this session
       c. eligible_to_send = (last_nudge is None)  # first nudge, gated only by 24h silence
                             or (now - last_nudge.sent_at >= frequency_hours)
       d. if eligible_to_send:
            hours = round((now - last_patient_message.sent_at).total_seconds() / 3600)
            text = await gateway.generate_nudge_message(disease, patient_name, patient_age, hours)
            save Message(role=patient, is_nudge=True, sent_at=now)
            send_push.delay(user_id, "PocketPatient", "Your patient replied",
                            {"type": "new_message", "session_id": str(session_id)})
    4. commit
```
Push copy intentionally matches a normal reply — week-10.md (L68) keeps nudges indistinguishable from regular patient messages from the student's perspective.

---

## `_reply_delay_seconds` (in `session_service.py`)

```python
_DELAY_RANGES_SEC = {
    "pressured":    (0, 0),
    "flat":         (3600, 4 * 3600),
    "tangential":   (15 * 60, 60 * 60),
    "disorganized": (0, 30 * 60),
}
_DEFAULT_DELAY_RANGE_SEC = (5 * 60, 30 * 60)

def _reply_delay_seconds(speech_style: str) -> float:
    lo, hi = _DELAY_RANGES_SEC.get(speech_style, _DEFAULT_DELAY_RANGE_SEC)
    return random.uniform(lo, hi)
```

---

## Data model: `sessions.pending_reply_task_id`

```python
pending_reply_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Tracks the Celery task id of the currently-scheduled `generate_and_send_reply` for a session. Mirrors the "DB state is the real guard, in-process check is the fast path" pattern already used for the active-session unique index in `initiate_case`:

- **Why a column over Redis:** durable, queryable, survives Redis restarts/flushes — a stale Redis entry would silently let a superseded reply fire with no way to detect it; a DB column lets the task self-check against committed state.
- **Why the supersede check matters:** `celery.control.revoke()` is best-effort — it cannot stop a task that's already executing. Without the in-task check, a race (new message arrives just as the old reply starts generating) could produce two patient replies for one student turn. The conditional UPDATE on clear (`WHERE pending_reply_task_id = :my_task_id`) closes the symmetric race on the way out.

Migration: `ALTER TABLE sessions ADD COLUMN pending_reply_task_id VARCHAR(255)`.

---

## `POST /api/v1/sessions/{session_id}/messages` (modified contract)

- **New query param:** `instant: bool = False`. When `true`, the reply is dispatched without an `eta` (fires as soon as a worker picks it up) — for manual/dev testing per week-10.md L24. The response contract is identical either way; only delivery timing changes.
- **Response:** `202 Accepted` + `MessageOut` — the **student's own saved message** (echoing back the server-assigned `id`/`sent_at`/`response_latency_sec` for the client to reconcile against its optimistic local copy). This replaces the old `201` + patient-reply-`MessageOut` contract.
- **Errors:** unchanged (`404` session not found / not owned, `409` session not active).

---

## `app/services/llm_gateway.py` — `generate_nudge_message`

```python
_NUDGE_PROMPT_TEMPLATE = (
    "You are the same patient. You sent a message {hours} hours ago and the doctor "
    "hasn't replied.\n"
    "Your nudge tone is: {tone}\n"
    "Example of your nudge style: {example}\n"
    "Write a short follow-up message (1-2 sentences) that is consistent with your condition."
)

async def generate_nudge_message(
    self, disease: Disease, patient_name: str, patient_age: int, hours_since_last_message: int
) -> str:
    system_prompt = self._build_system_prompt(disease, patient_name, patient_age)
    nudge = disease.nudge_behavior
    prompt = _NUDGE_PROMPT_TEMPLATE.format(
        hours=hours_since_last_message,
        tone=nudge.get("tone", ""),
        example=nudge.get("example", ""),
    )
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    response = await asyncio.to_thread(
        self.client.models.generate_content,
        model=self.model,
        contents=contents,
        config=self._gen_config(system_prompt),
    )
    if not response.text:
        raise HTTPException(status_code=502, detail="LLM returned empty nudge response")
    return response.text
```

Reuses `_build_system_prompt` (same in-character constraints as regular replies) — only the user-turn prompt is nudge-specific. `nudge_behavior["example"]` may be an empty string (per `disease_parser.py`); the template degrades gracefully (an empty "Example of your nudge style:" line still reads fine to the LLM).

---

## `check_and_send_nudges` — eligibility query notes

- **"Active session" + "last message from patient" + "silent ≥ 24h":** implemented as a single query against the latest message per active session (`DISTINCT ON (session_id) ... ORDER BY session_id, sent_at DESC`), filtered to `role == patient` and `sent_at <= now - 24h`. Avoids an N+1 of "load full transcript per active session."
- **Cadence is derived, not stored** (per design discussion): the most recent `Message` with `is_nudge=True` acts as the "last nudge sent" marker — no new column. First nudge is gated solely by the 24h silence; subsequent nudges repeat at `frequency_hours` intervals measured from the last nudge's `sent_at`.
- **Hourly beat cadence is coarse enough** that no dedup-key/jitter scheme (like `initiate_case`'s Redis `SET NX`) is needed — unlike the 15-minute case-initiation beat with in-window random ETAs, there's no risk of piling up duplicate scheduled tasks here; everything resolves within a single beat-task run.

---

## Response latency tracking (Task 3)

- `response_latency_sec` per student message: **already implemented** (`session_service.py` — computed as the gap between the prior patient message's `sent_at` and the student message's `sent_at`).
- `session.avg_response_latency_sec`: **currently only refreshed in `grading_service.grade_diagnosis`** (at diagnosis time). The spec wants it refreshed after every student message — the recompute moves into `handle_student_message` (step 3 in the data flow above). The mean-of-non-null-latencies helper (`_avg_student_latency`) relocates from `grading_service` to `session_service` — `grading_service` already imports `get_session_messages` from `session_service` (one-directional dependency), so `session_service` is the natural lower-level home; `grading_service` then imports the helper from there instead of keeping a private copy. `grade_diagnosis` keeps calling it too (harmless — it recomputes the same value the message-send path already set, guaranteeing the metric is current at grading time even if some intermediate path is ever bypassed).

---

## Error handling

| Scenario | Behavior |
|----------|----------|
| `generate_and_send_reply` fires but a newer message has superseded it | Self-check against `pending_reply_task_id` — returns without generating a reply or sending a push |
| LLM call fails transiently inside `generate_and_send_reply` | `bind=True, max_retries=3, default_retry_delay=60` — same retry shape as `send_push` |
| `celery.control.revoke()` fails or the old task is already executing | Best-effort only; the supersede self-check is the real guard (mirrors the "unique index is the real guard" note in the Week 9 spec) |
| Disease has no `nudge_behavior.frequency` match (e.g. malformed/legacy data) | Defaults to `medium` (24h) cadence |
| `nudge_behavior["example"]` is empty string | Template renders with an empty example line — LLM still receives tone + instructions |

---

## Testing

Following existing patterns (`test_session_service.py`, `test_case_initiation.py`, `test_push_task.py`, `test_sessions_router.py`):

- **`handle_student_message`**: per-`speech_style` delay ranges (including `pressured` = 0 and unknown styles → default range), `instant=true` bypasses delay, revoke+reschedule replaces `pending_reply_task_id`, `avg_response_latency_sec` recomputed after each student message
- **`generate_and_send_reply`**: superseded task skips silently (no reply/push), normal flow saves reply + increments `turn_count` + dispatches push + clears `pending_reply_task_id`, conditional clear doesn't clobber a newer id
- **`check_and_send_nudges`**: 24h-silence + last-message-role gate, first-nudge-vs-repeat cadence per frequency tier (`high`/`medium`/`low`/unrecognized), nudge message saved with `is_nudge=True`, push dispatched with matching copy
- **Router**: `202` status + echoed student message body, `?instant=true` query param plumbing, existing `404`/`409` error paths unchanged
