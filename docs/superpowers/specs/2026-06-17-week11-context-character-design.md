# Week 11 Backend — Context Management + Character Consistency (Design)

**Date:** 2026-06-17
**Source:** `week-11.md` → Dev A (Mahir) backend tasks
**Theme:** Full-transcript context management, character-consistency guardrails, conversation analytics.

## Goals

1. Send the **full raw transcript** to the LLM on every patient reply, with token
   accounting and a sliding-window fallback for very long cases.
2. Keep the AI patient **in character**: detect fourth-wall breaks and diagnosis-name
   leaks, regenerate, and fall back to a generic in-character line.
3. Provide a read-only **`get_session_stats`** helper for post-session analytics
   (feeds Phase 3 dashboards).

## Non-goals / YAGNI

- No analytics HTTP endpoint yet — Phase 3 owns dashboards. `get_session_stats` is a
  service helper only.
- No transcript summarization — pathological speech patterns (clanging, pressured
  speech) must be preserved verbatim.
- No new infrastructure; reuse the existing Celery `bot_reply` path and `MessageRole`.

## Existing state (already in the codebase)

- `Message.token_count: int | None` column exists but is **never populated**.
- `MessageRole.system` exists in the enum.
- `bot_reply.py` already sends the full transcript as `conversation_history` (built
  with an inline comprehension mapping `student → "user"`, else `"model"`).
- `Session.turn_count` and `Session.avg_response_latency_sec` exist and are maintained.

So the work is additive: populate `token_count`, add windowing, add the guardrail
orchestrator, and add the analytics helper.

## Architecture

Three small, independently testable units:

| Task | Unit | Depends on |
|------|------|-----------|
| 1 | `app/services/context_window.py` (new) | `tiktoken`, `Message` |
| 2 | `app/services/character_guardrail.py` (new) | `Message`, `Disease.name`, DB session |
| 3 | `app/services/analytics_service.py` (new) + `SessionStats` schema | `Session`, `Message`, `Disease` |

The LLM gateway methods (`generate_patient_message`, `generate_opening_message`) keep
their current `-> str` signatures and stay DB-free. The retry/fallback loop and the
system-message logging live in `character_guardrail`, invoked by the callers
(`bot_reply`, `create_new_session`) which already hold a DB session.

### Migration check

No new columns are expected. Before implementing, verify the Postgres `message_role`
enum in `alembic/versions/` actually contains `'system'`; if a prior migration created
the enum without it, add a migration (`ALTER TYPE message_role ADD VALUE 'system'`).
Tests use `Base.metadata.create_all`, so they are unaffected either way — the check is
for the dev/prod DB.

## Task 1 — Full transcript context management

### `app/services/context_window.py`

Constants:

```python
MAX_CONTEXT_TOKENS = 100_000   # send everything below this
WARN_CONTEXT_TOKENS = 50_000   # log a warning above this
HEAD_KEEP = 5                  # first N messages always retained in the window
OMITTED_NOTE = "[Earlier messages omitted for length]"
```

Functions:

- `count_tokens(text: str) -> int` — uses `tiktoken.get_encoding("cl100k_base")`
  (encoding cached at module load). **Note:** tiktoken is a GPT tokenizer, not Gemini's;
  the spec mandates it and we accept it as a close-enough budgeting approximation.
- `build_history(messages: list[Message]) -> list[dict]` — the single place the Gemini
  `contents` list is assembled. It:
  1. **Filters out `MessageRole.system`** rows (internal notes must never reach the LLM;
     the old inline mapping would have sent them as `"model"`).
  2. Maps each remaining message to `{"role": "user" if student else "model",
     "parts": [{"text": content}]}`.
  3. Computes total tokens as `sum(m.token_count if not None else count_tokens(content))`.
  4. Logs `logger.warning(...)` with the session-identifying info if total >
     `WARN_CONTEXT_TOKENS`.
  5. If total > `MAX_CONTEXT_TOKENS`, applies the sliding window: keep the first
     `HEAD_KEEP` messages, then add trailing messages from newest backward while they
     fit under the budget, and prepend an `OMITTED_NOTE` entry (as a `"user"` part, since
     Gemini `contents` has no system role). Head + tail never overlap.

`bot_reply.py` replaces its inline comprehension with `history = build_history(messages)`.

### Populating `token_count`

Set `token_count=count_tokens(content)` on every `Message(...)` creation:
- `handle_student_message` (student message)
- `bot_reply._generate_and_send` (patient reply)
- `create_new_session` (opening message)
- `nudge` task (nudge message)
- `character_guardrail` system rows: `token_count` left `None` (never sent to LLM).

## Task 2 — Character consistency guardrails

### `app/services/character_guardrail.py`

Constants:

```python
MAX_RETRIES = 2
FALLBACK_TEXT = "I don't know, doctor... I just don't feel right"
AI_BREAK_PHRASES = ["as an ai", "i'm a language model", "i am a language model",
                    "i don't actually have", "as a language model", "i'm an ai"]
```

Functions:

- `check_character_break(text: str, disease_name: str) -> str | None`
  - Returns `"ai_break"` if any `AI_BREAK_PHRASES` substring appears (case-insensitive).
  - Returns `"diagnosis_leak"` if `disease_name` (case-insensitive, whole-name substring)
    appears in the text.
  - Returns `None` if the text is in-character.
- `async generate_in_character(generate_fn, *, disease_name, db, session_id) -> str`
  - `generate_fn` is a zero-arg async callable returning the raw LLM text (a closure over
    the gateway call, so this orchestrator stays gateway-agnostic).
  - Loop: initial attempt + up to `MAX_RETRIES` regenerations.
    - On each violating attempt: `db.add(...)` a `MessageRole.system` row with
      `content=f"[regenerated: {reason}]"`, `sent_at=now`, `is_nudge=False`,
      `token_count=None`. Then regenerate.
  - If all attempts violate: `db.add(...)` a system row `content=f"[fallback used: {reason}]"`
    and return `FALLBACK_TEXT`.
  - Otherwise return the first clean text.
  - **Does not commit** — the caller's existing `db.commit()` flushes the system rows in
    the same transaction as the reply/opening message.

### Wiring

- `bot_reply._generate_and_send`: instead of `reply_text = await gateway.generate_patient_message(...)`,
  call `reply_text = await generate_in_character(lambda: gateway.generate_patient_message(disease, name, age, history), disease_name=disease.name, db=db, session_id=session.id)`.
- `create_new_session`: same wrapping around `generate_opening_message`.

System rows are written to the same session; because `build_history` filters them out,
they never pollute later LLM context, and they are queryable per session for debugging.

## Task 3 — Conversation analytics

### `SessionStats` schema (`app/schemas/session.py`)

```python
class SessionStats(BaseModel):
    total_turns: int
    total_duration_sec: float | None      # (completed_at or now) - started_at
    avg_response_latency_sec: float | None
    student_msg_len_avg: float | None      # character count
    student_msg_len_min: int | None
    student_msg_len_max: int | None
    topic_coverage_score: float            # 0.0–1.0
    topics_covered: list[str]
    topics_missed: list[str]
```

### `app/services/analytics_service.py`

`async get_session_stats(session_id, db) -> SessionStats`:

- Loads the session; raises `ValueError` if it does not exist (there is no HTTP endpoint
  yet to map to a 404 — Phase 3 will wrap it).
- `total_turns = session.turn_count`.
- `total_duration_sec = ((session.completed_at or now) - session.started_at).seconds`.
- `avg_response_latency_sec = session.avg_response_latency_sec`.
- Student message lengths: load student messages, compute avg/min/max of `len(content)`
  (character count). Empty → all `None`.
- `topic_coverage`: for each `disease.key_symptoms` entry, mark covered if its lowercased
  text appears as a substring in the concatenated lowercased student-message text.
  `topic_coverage_score = len(covered) / len(key_symptoms)` (0.0 if no symptoms).

## Testing (TDD)

New unit tests:
- `tests/test_context_window.py` — `count_tokens` monotonicity, `build_history` filters
  system rows, role mapping, warning above 50K, sliding window keeps head + tail + note
  above 100K, no window below 100K.
- `tests/test_character_guardrail.py` — `check_character_break` detects each phrase and
  the diagnosis name, passes clean text; `generate_in_character` returns clean text with
  no system rows, regenerates and logs a system row on a break, falls back after 2 retries.
- `tests/test_analytics_service.py` — duration, latency, message-length aggregation,
  topic coverage (full / partial / none), empty-session edge cases.

Extend existing:
- `tests/test_bot_reply.py` — patient reply gets `token_count`; system messages excluded
  from history; guardrail wiring (regeneration on a mocked break).
- `tests/test_session_service.py` — opening message gets `token_count`; guardrail wiring.

All tests follow the existing `clean_tables` + `professor`/`student` fixture pattern and
mock the gateway (no live LLM calls).

## Risks / notes

- tiktoken token counts diverge from Gemini's true tokenization; the 100K/50K thresholds
  are conservative enough to absorb the error.
- Diagnosis-leak detection is a plain substring match — short disease names risk false
  positives. Acceptable for v1; refine in a later week if it misfires.
