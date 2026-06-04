# Week 8 Backend Design — Diagnosis Submission + Grading Engine

**Date:** 2026-06-03
**Phase:** 2 — Core Simulation
**Theme:** Students submit a diagnosis for their AI-patient case; a Gemini-backed
grading engine evaluates it, returns a score + feedback on success or a subtle
hint on failure, and reveals the case's unit/disease once correctly diagnosed.

---

## Goals

1. Persist diagnosis scores in a new `scores` table (one row per session).
2. Add `POST /api/v1/sessions/{session_id}/diagnose` for students to submit.
3. Build a grading service that calls Gemini for clinical evaluation and
   computes a time-weighted total score.
4. On a correct diagnosis, mark the session `diagnosed`, set `completed_at`, and
   reveal the unit label + disease name.
5. Extend `GET /api/v1/sessions/{id}` to include score + reveal for completed
   sessions (and only for completed sessions — no answer leak while active).

## Non-goals

- Multi-attempt score history / audit trail (incorrect attempts persist nothing).
- Professor-tunable weights UI (weights/thresholds are module constants now,
  designed to be promoted to per-course config later).
- Frontend work (Dev B owns the submission form, result display, history view).

---

## Architecture

Four layers, mirroring the Week 7 session feature:

| Layer | File | Responsibility |
|-------|------|----------------|
| Model | `app/models/score.py` | `Score` SQLAlchemy model (one per table) |
| Schema | `app/schemas/session.py` (extend) | `DiagnosisCreate`, `DiagnosisResult`, `ScoreOut`, `RevealOut`; extend `SessionOut` |
| Gateway | `app/services/llm_gateway.py` (extend) | `grade_diagnosis` (structured JSON), `generate_hint` (plain text) |
| Service | `app/services/grading_service.py` (new) | `compute_response_time_score`, `grade_diagnosis` orchestration |
| Router | `app/routers/sessions.py` (extend) | `POST /sessions/{id}/diagnose`; extend `GET /sessions/{id}` reveal |
| Migration | `alembic/versions/*` | create `scores` table |

The LLM gateway remains a module-level lazy singleton (`gateway`) so tests
monkeypatch `app.services.grading_service.gateway` without DI plumbing.

---

## Data model: `scores`

New file `app/models/score.py`, re-exported from `app/models/__init__.py`.

| Column | Type | Constraints |
|--------|------|-------------|
| id | UUID | PK, `default=uuid.uuid4` |
| session_id | UUID | FK → `sessions.id` `ondelete=CASCADE`, **UNIQUE**, NOT NULL |
| primary_dx | VARCHAR(255) | NOT NULL |
| differentials | JSONB | array of strings (≤3); NOT NULL, `server_default='[]'`-equivalent (default `list`) |
| justification | TEXT | nullable |
| is_correct | BOOLEAN | nullable |
| rubric_score | FLOAT | 0–100 |
| response_time_score | FLOAT | 0–100 |
| total_score | FLOAT | 0–100 |
| feedback_text | TEXT | nullable |
| graded_at | TIMESTAMP(tz) | nullable |
| created_at | TIMESTAMP(tz) | `server_default=func.now()` |

**Persistence rule:** a `Score` row is created **only when the diagnosis is
correct**. Incorrect attempts write nothing to the database — the session stays
active and the student may resubmit freely. This keeps the `UNIQUE(session_id)`
constraint clean and avoids upsert logic.

Migration is hand-verified for the UNIQUE constraint and CASCADE per project
convention (autogenerate may miss these).

---

## Grading service — `app/services/grading_service.py`

Module constants (designed to be promoted to per-course config later):

```python
RUBRIC_WEIGHT = 0.7
TIME_WEIGHT = 0.3
TIME_SCORE_FULL = 100.0
TIME_SCORE_FLOOR = 50.0
TIME_SCORE_NEUTRAL = 75.0            # used when no latency data exists
GRACE_LATENCY_SEC = 30 * 60          # 30 min → still full score
FLOOR_LATENCY_SEC = 24 * 60 * 60     # 24 h → floored
```

### `compute_response_time_score(avg_latency_sec: float | None) -> float`

Floored linear decay tuned for an **asynchronous** messaging app (students reply
between classes over hours, not in real time):

- `None` → `75.0` (neutral — missing data is not rewarded with a perfect score)
- `avg ≤ 1800s` (30 min) → `100.0`
- `1800s < avg < 86400s` (24 h) → linear interpolation `100 → 50`
- `avg ≥ 86400s` → `50.0` (floor)

### `grade_diagnosis(session, submission, db) -> Score`

1. Load the session's `Disease` and the full transcript (`get_session_messages`).
2. Compute avg latency from **student** messages' `response_latency_sec`
   (ignoring `None`s); persist the result onto `session.avg_response_latency_sec`
   so the field is no longer perpetually null.
3. Build a plain-text transcript string and call
   `gateway.grade_diagnosis(disease, submission, transcript)` →
   `{"is_correct": bool, "rubric_score": float, "feedback": str}`.
4. `time_score = compute_response_time_score(avg_latency)`.
5. `total = round(RUBRIC_WEIGHT * rubric_score + TIME_WEIGHT * time_score, 2)`.
6. Construct and **return** a `Score` object (not committed) with `graded_at=now`.
   The router decides whether to persist it (correct) or discard it (incorrect).

The service does not commit; the router owns the transaction boundary.

---

## LLM gateway additions — `app/services/llm_gateway.py`

Both new methods use `gemini-2.5-flash` (same model as the patient gateway),
thinking disabled, wrapped in `asyncio.to_thread`, raising `HTTPException(502)`
on empty/unparseable output — matching the existing convention.

### `grade_diagnosis(disease, submission, transcript) -> dict`

- Uses **structured JSON output**: `GenerateContentConfig` with
  `response_mime_type="application/json"` and a `response_schema` describing
  `{is_correct: bool, rubric_score: number, feedback: string}`.
- Prompt (from the Week 8 spec):

  > You are a clinical evaluation system. The student was diagnosing a patient
  > with {disease.name} ({disease.dsm_code}).
  >
  > The student's diagnosis:
  > - Primary: {submission.primary_dx}
  > - Differentials: {submission.differentials}
  > - Justification: {submission.justification}
  >
  > The conversation transcript: {transcript}
  >
  > Evaluate: (1) primary correct (exact or clinically equivalent), (2) any
  > differentials correct, (3) justification quality (references specific
  > symptoms from the conversation). Respond in JSON:
  > {"is_correct": bool, "rubric_score": 0-100, "feedback": "..."}

- Parse `response.text` as JSON; on empty text or `JSONDecodeError` →
  `HTTPException(502, "LLM returned ... grading response")`.
- Clamp `rubric_score` into `[0, 100]` defensively.

### `generate_hint(wrong_dx: str, actual_dx: str) -> str`

- Plain-text generation (no JSON schema), thinking disabled.
- Prompt: "The student guessed {wrong_dx}. The actual condition is {actual_dx}.
  Give a subtle hint that redirects without revealing the answer. Do not name
  the actual condition."
- Empty → `HTTPException(502)`.

---

## Endpoint — `POST /api/v1/sessions/{session_id}/diagnose`

- Auth: `require_role("student")`; ownership enforced by `Session.user_id ==
  current_user.id` (returns **404** if not owner, per project convention — same
  pattern as `send_message`).
- **409** if `session.status != active`.
- Request body `DiagnosisCreate`:
  ```json
  {"primary_dx": "Major Depressive Disorder",
   "differentials": ["Bipolar II", "Adjustment Disorder"],
   "justification": "Patient presents with..."}
  ```
  - `primary_dx`: `str`, `min_length=1`
  - `differentials`: `list[str]`, default `[]`, `max_length=3`
  - `justification`: `str`, `min_length=50` (enforced server-side — never trust
    the client; a "idk" justification is rejected with 422 even if the frontend
    has a bug. The frontend enforces the same 50-char rule for UX.)

### Correct path
1. `grade_diagnosis(...)` returns a `Score` with `is_correct=True`.
2. Persist the `Score`; set `session.status='diagnosed'`, `session.completed_at=now`.
3. Commit.
4. Return `DiagnosisResult`:
   ```json
   {"correct": true,
    "score": { ...ScoreOut... },
    "reveal": {"disease_name": "...", "dsm_code": "...", "unit_label": "..."}}
   ```

### Incorrect path
1. `grade_diagnosis(...)` returns `is_correct=False` — **discard** the Score object.
2. Call `gateway.generate_hint(primary_dx, disease.name)`.
3. No DB write to `scores`; `session.avg_response_latency_sec` update may still be
   committed (harmless, keeps the metric fresh). Session stays `active`.
4. Return:
   ```json
   {"correct": false, "hint": "Consider re-examining the patient's speech patterns"}
   ```

`DiagnosisResult` is a single Pydantic model: `correct: bool` always present;
`score: ScoreOut | None`, `reveal: RevealOut | None`, `hint: str | None`
(correct path fills score+reveal, incorrect fills hint).

---

## Reveal on `GET /api/v1/sessions/{id}`

Extend `SessionOut`:

```python
score: ScoreOut | None = None
reveal: RevealOut | None = None
```

Populated **only when `session.status == 'diagnosed'`**: join the `Score` row +
`Disease` + `Unit` to build `score` and `reveal`. While the session is `active`,
both fields are `null` — the disease name is never sent to the client before a
correct diagnosis. Applies identically to the student-owner and
professor-of-course viewers; existing auth logic is unchanged.

`ScoreOut` fields: `primary_dx, differentials, justification, is_correct,
rubric_score, response_time_score, total_score, feedback_text, graded_at`.
`RevealOut` fields: `disease_name, dsm_code, unit_label`.

---

## Error handling summary

| Condition | Status |
|-----------|--------|
| Not a student | 403 (from `require_role`) |
| Session not found / not owned | 404 |
| Session not `active` | 409 |
| Invalid body (empty primary_dx, >3 differentials, justification <50 chars) | 422 |
| Gemini empty/unparseable | 502 |

---

## Testing (TDD — failing test → minimal impl → green → commit)

### `tests/test_grading_service.py`
- `compute_response_time_score`: `None`→75, `0`→100, `1800`→100, `86400`→50,
  beyond→50, an interior midpoint (e.g. ~12.5h ≈ 75).
- `grade_diagnosis` (mock `grading_service.gateway`): correct path returns Score
  with `is_correct=True` and `total = 0.7*rubric + 0.3*time`; avg-latency
  computed from student messages; incorrect path returns `is_correct=False`.

### `tests/test_llm_gateway.py` (extend)
- `grade_diagnosis`: parses JSON `response.text`; sets `response_mime_type` +
  schema + thinking disabled; empty text → 502; malformed JSON → 502.
- `generate_hint`: returns text; empty → 502.

### `tests/test_sessions_router.py` (extend)
- Diagnose correct: 200/201, `correct:true`, score + reveal present, session
  becomes `diagnosed`, `completed_at` set, a `scores` row exists.
- Diagnose incorrect: `correct:false`, `hint` present, **no** `scores` row,
  session still `active`.
- 409 when session not active; 404 when not owner; 403 for professor;
  422 for empty `primary_dx`, for >3 differentials, and for justification <50 chars.
- `GET /sessions/{id}`: reveal+score present once diagnosed; both `null` while
  active.

Gateway is mocked in router/service tests via `AsyncMock`; the genai SDK is
mocked in gateway unit tests (`patch("app.services.llm_gateway.genai")`).

---

## Docs

Update `docs/api-contract.md`: add the `POST /sessions/{id}/diagnose` row +
request/response/errors, and note the `score`/`reveal` additions to
`GET /sessions/{id}`.

---

## Open questions resolved during brainstorming

- **Incorrect diagnosis persistence:** no `Score` row — hint only.
- **response_time_score thresholds:** async-tuned (30 min grace, 24 h floor,
  neutral 75 when no data) rather than real-time seconds.
- **Grading model:** `gemini-2.5-flash` with structured JSON output.
- **Incorrect response shape:** minimal — `{correct:false, hint}` (no attempt count).
