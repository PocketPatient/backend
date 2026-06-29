# API Contract

Base URL (local dev): `http://localhost:8000/api/v1`

---

## Auth

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| POST | `/api/v1/auth/login` | Verify Firebase ID token; returns JWT + refresh token | None | ✅ Week 2 |
| POST | `/api/v1/auth/refresh` | Rotate refresh token; returns new JWT + refresh token | None | ✅ Week 2 |

### POST /api/v1/auth/login
**Request:** `{"firebase_id_token": "..."}`  
**Response:** `{"access_token": "...", "refresh_token": "...", "token_type": "bearer"}`  
**Errors:** 401 invalid Firebase token, 403 non-Rutgers email

### POST /api/v1/auth/refresh
**Request:** `{"refresh_token": "..."}`  
**Response:** `{"access_token": "...", "refresh_token": "...", "token_type": "bearer"}`  
**Errors:** 401 expired or invalid refresh token

---

## Users

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| GET | `/api/v1/users/me` | Get current user profile | Bearer JWT | ✅ Week 2 |
| PUT | `/api/v1/users/me/role` | Set role (once only) | Bearer JWT | ✅ Week 2 |
| PUT | `/api/v1/users/me/fcm-token` | Store or replace FCM push token | Bearer JWT | ✅ Week 9 |
| PUT | `/api/v1/users/me/notification-preferences` | Set push on/off + quiet hours | Bearer JWT | ✅ Week 15 |

### GET /api/v1/users/me
**Response:** `UserOut` — id, google_uid, email, role, is_verified, display_name, created_at  
**Errors:** 401 missing/invalid token

### PUT /api/v1/users/me/role
**Request:** `{"role": "student" | "professor"}`  
**Response:** updated `UserOut`  
**Errors:** 401 unauthenticated, 409 role already set, 422 invalid role value  
**Side effects:** student → `is_verified=true`; professor → `is_verified=false` (pending approval)

### PUT /api/v1/users/me/fcm-token
**Auth:** any authenticated user (student or professor)  
**Request:** `{"fcm_token": "<string, 1–512 chars>"}`  
**Response (200):** `UserOut` (same shape as `GET /users/me`)  
**Errors:** 401 unauthenticated, 422 `fcm_token` is empty or missing  
**Notes:** The `fcm_token` field is write-only — it is NOT included in `UserOut`. This endpoint is idempotent; calling it again replaces the stored token.

### PUT /api/v1/users/me/notification-preferences
**Auth:** any authenticated user  
**Request:** `{"push_enabled": true, "quiet_hours_start": "22:00", "quiet_hours_end": "08:00"}`  
- `push_enabled` (bool, required)
- `quiet_hours_start` / `quiet_hours_end` (`HH:MM`, optional) — must be sent **both or neither**. A window where start > end wraps past midnight. Times are interpreted as UTC.

**Response (200):** the saved preferences (times serialized as `HH:MM:SS`, `null` when no quiet window).  
**Errors:** 401 unauthenticated, 422 only one of start/end provided  
**Behavior:** When a push is raised, `send_push` drops it if `push_enabled` is false; if the current time is inside the quiet window it re-enqueues the push with an ETA at the window's close (delivered when quiet hours end) rather than dropping it.

---

## Courses

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| POST | `/api/v1/courses` | Create a course (professor only) | Bearer JWT | ✅ Week 3 |
| GET | `/api/v1/courses` | List courses for current user | Bearer JWT | ✅ Week 3 |
| GET | `/api/v1/courses/{id}` | Get course details | Bearer JWT | ✅ Week 3 |
| PUT | `/api/v1/courses/{id}` | Update course (professor owner) | Bearer JWT | ✅ Week 3 |
| DELETE | `/api/v1/courses/{id}/deactivate` | Deactivate course (professor owner) | Bearer JWT | ✅ Week 3 |

### POST /api/v1/courses
**Role required:** professor  
**Request:** `{"title": "Psychiatry 101", "semester": "Fall 2026"}`  
**Response:** `CourseOut` — id, title, professor_id, class_code (6-char alphanumeric, uppercase, no ambiguous chars), semester, is_active, msg_window_start, msg_window_end, msg_timezone, created_at, student_count  
**Errors:** 401 unauthenticated, 403 not a professor

### GET /api/v1/courses
**Response:** array of `CourseOut`  
- Professors: courses they own  
- Students: courses they are enrolled in

### GET /api/v1/courses/{id}
**Response:** `CourseOut`  
**Errors:** 404 not found or user is not owner/enrolled (existence not leaked)

### PUT /api/v1/courses/{id}
**Role required:** professor (must own the course)  
**Request (all fields optional):** `{"title": "...", "semester": "...", "msg_window_start": "HH:MM:SS", "msg_window_end": "HH:MM:SS", "msg_timezone": "..."}`  
**Response:** updated `CourseOut`  
**Errors:** 404 not found or not owner  
**Validation:** Validates IANA timezone and msg_window_start < msg_window_end (422 on invalid input).

### DELETE /api/v1/courses/{id}/deactivate
**Role required:** professor (must own the course)  
**Response:** updated `CourseOut` with `is_active: false`  
**Errors:** 404 not found or not owner

---

## Enrollments

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| POST | `/api/v1/enrollments/join` | Join a course via class code | Bearer JWT | ✅ Week 3 |
| GET | `/api/v1/courses/{id}/students` | List enrolled students (professor owner) | Bearer JWT | ✅ Week 3 |
| DELETE | `/api/v1/courses/{id}/students/{user_id}` | Remove a student (professor owner) | Bearer JWT | ✅ Week 3 |

### POST /api/v1/enrollments/join
**Role required:** student  
**Request:** `{"class_code": "ABC123"}`  
**Response:** `CourseOut` for the joined course (with updated student_count)  
**Errors:** 401 unauthenticated, 403 not a student, 404 invalid code, 409 already enrolled, 410 course inactive

### GET /api/v1/courses/{id}/students
**Role required:** professor (must own the course)  
**Response:** array of `{"user_id", "email", "display_name", "enrolled_at"}`  
**Errors:** 404 not found or not owner

### DELETE /api/v1/courses/{id}/students/{user_id}
**Role required:** professor (must own the course)  
**Response:** 204 No Content  
**Errors:** 404 course not found or not owner, 404 student not enrolled

---

## Disease Documents

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| POST | `/api/v1/courses/{course_id}/disease-document` | Upload a disease document (CSV or JSON) and return preview | Bearer JWT | ✅ Week 4 |
| POST | `/api/v1/courses/{course_id}/disease-document/confirm` | Commit the most recent pending upload — replaces existing units | Bearer JWT | ✅ Week 4 |

### POST /api/v1/courses/{course_id}/disease-document
**Role required:** professor (must own the course)
**Request:** `multipart/form-data` with a single `file` field (`.csv` or `.json`)
**Behavior:** Stores the raw file, creates a `disease_documents` row with the next per-course version, parses and returns a preview. Does **not** create Unit/Disease rows.
**Response (200):**
```json
{
  "document_id": "uuid",
  "version": 1,
  "units": [
    {"label": "Unit 1: Mood Disorders", "disease_count": 3, "diseases": ["MDD", "Bipolar I", "Bipolar II"]}
  ],
  "errors": [
    {"location": "row 5", "message": "missing required field: difficulty_tier"}
  ]
}
```
**Errors:** 400 unsupported extension, 401 unauthenticated, 403 not a professor, 404 course not found or not owner

### POST /api/v1/courses/{course_id}/disease-document/confirm
**Role required:** professor (must own the course)
**Behavior:** Finds the latest unparsed upload for this course, re-reads and re-parses the file, then (if no parse errors and no released units) deletes existing units and inserts the new ones atomically. Sets `parsed_at` on the document row.
**Response (200):**
```json
{"document_id": "uuid", "version": 1, "units_created": 2, "diseases_created": 6}
```
**Errors:**
- 400 — parse errors present (`detail.errors` lists them); nothing committed
- 401 — unauthenticated
- 403 — not a professor
- 404 — course not found, or no pending upload to confirm
- 409 — at least one existing unit has `status = 'released'`
- 410 — upload file no longer exists on disk (re-upload required)

---

## Units

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| GET | `/api/v1/courses/{course_id}/units` | List units (professor: all statuses with diseases; student: released only, no disease details) | Bearer JWT | ✅ Week 5 |
| PUT | `/api/v1/courses/{course_id}/units/{unit_id}/release` | Release a draft unit (sets status=released, release_date=now) | Bearer JWT (professor) | ✅ Week 5 |
| PUT | `/api/v1/courses/{course_id}/units/{unit_id}/close` | Close a released unit | Bearer JWT (professor) | ✅ Week 5 |
| GET | `/api/v1/courses/{course_id}/disease-pool` | All active diseases from released units — used by scheduler | Bearer JWT (professor) | ✅ Week 5 |

### GET /api/v1/courses/{course_id}/units
**Professor (course owner):** Returns all units (draft/released/closed) with `diseases` list (active only).  
**Student (enrolled):** Returns only `released` units. No `diseases` field — students are blind to disease details.  
**Errors:** 404 if course not found or caller is not owner/enrolled.

### PUT /api/v1/courses/{course_id}/units/{unit_id}/release
**Role required:** professor (must own course)  
**Response:** updated `UnitOut` with `status: "released"` and `release_date` set  
**Errors:** 404 not found, 409 unit is not in draft status

### PUT /api/v1/courses/{course_id}/units/{unit_id}/close
**Role required:** professor (must own course)  
**Response:** updated `UnitOut` with `status: "closed"`  
**Errors:** 404 not found, 409 unit is not released

### GET /api/v1/courses/{course_id}/disease-pool
**Role required:** professor (must own course). Not exposed to students.  
**Response:** `list[DiseaseOut]` — id, name, category, difficulty_tier  
**Errors:** 404 not found or not owner

---

## Sessions

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| POST | `/api/v1/sessions` | Start a new case — picks a disease, generates the AI patient's opening message | Bearer JWT (student) | ✅ Week 7 |
| GET | `/api/v1/sessions/active?course_id={id}` | Active session for this course (with messages) | Bearer JWT (student) | ✅ Week 7 |
| GET | `/api/v1/sessions/{session_id}` | Session detail with all messages | Bearer JWT (owner or course professor) | ✅ Week 7 |
| POST | `/api/v1/sessions/{session_id}/messages` | Send a student reply; the AI patient's response is generated and pushed asynchronously | Bearer JWT (student owner, active session) | ✅ Week 10 |
| POST | `/api/v1/sessions/{session_id}/diagnose` | Submit a diagnosis; grade or hint | Bearer JWT (student owner) | ✅ Week 8 |

`SessionOut` — id, disease_id, course_id, status (`active`/`diagnosed`/`abandoned`), turn_count, started_at, messages (`list[MessageOut]`), and (diagnosed only) `score` (ScoreOut) + `reveal` (disease_name, dsm_code, unit_label).
`MessageOut` — id, role (`student`/`patient`/`system`), content, sent_at, response_latency_sec.

### POST /api/v1/sessions
**Role required:** student (must be enrolled in the course)
**Request:** `{"course_id": "uuid"}`
**Behavior:** Selects a random disease from the course's released-unit pool, creates an `active` session, and calls the LLM to generate the patient's opening message. A student may have at most one active session per course (enforced by a partial unique index).
**Response (201):** `SessionOut` with a single patient message.
**Errors:** 401 unauthenticated, 403 not a student, 404 not enrolled in course, 409 active session already exists for this course, 422 no diseases in the course pool, 502 LLM returned an empty response

### GET /api/v1/sessions/active
**Role required:** student
**Query:** `course_id` (uuid, required)
**Response (200):** `SessionOut` for the active session, messages ordered by `sent_at`.
**Errors:** 401 unauthenticated, 403 not a student, 404 no active session for this course

### GET /api/v1/sessions/{session_id}
**Auth:** session owner (student) **or** the professor who owns the session's course.
**Response (200):** `SessionOut` with all messages. For `diagnosed` sessions, also includes `score` (ScoreOut) and `reveal` (disease_name, dsm_code, unit_label); both fields are `null` while the session is `active`.
**Errors:** 401 unauthenticated, 404 not found or caller not authorized (existence not leaked)

### POST /api/v1/sessions/{session_id}/messages
**Role required:** student (must own the session; session must be `active`)
**Query:** `instant` (bool, optional, default `false`) — when `true`, the patient's reply is dispatched without a delay (fires as soon as a Celery worker picks it up); for manual/dev testing.
**Request:** `{"content": "Tell me more about your symptoms"}` (non-empty)
**Behavior:** Records the student message with `response_latency_sec` (seconds since the last patient message), recomputes `session.avg_response_latency_sec` over all of the student's messages in the session, and schedules a delayed `generate_and_send_reply` Celery task to generate and save the patient's reply (any previously-queued reply for this session is revoked first). Unless `instant=true`, the task's ETA is offset by a random delay drawn from a range keyed on the disease's `speech_style` (e.g. `pressured` replies near-instantly, `flat` replies after roughly 1–4 hours). The reply itself — LLM call, persisted `Message`, `turn_count` increment, and push notification — happens out-of-band in the Celery task, not in this request.
**Response (202):** `MessageOut` — the student's own message, echoed back (not the patient's reply).
**Errors:** 401 unauthenticated, 403 not a student, 404 not found or not owner, 409 session is not active, 422 empty content

### POST /api/v1/sessions/{session_id}/diagnose
**Role required:** student (session owner)  
**Request:** `{"primary_dx": "Major Depressive Disorder", "differentials": ["Bipolar II", "Adjustment Disorder"], "justification": "Patient presents with... (min 50 chars)"}`  
**Response (correct):** `{"correct": true, "score": ScoreOut, "reveal": {"disease_name": "...", "dsm_code": "...", "unit_label": "..."}}` — session becomes `diagnosed`, `completed_at` set, a Score row is persisted, and any queued `generate_and_send_reply` task is best-effort revoked and `pending_reply_task_id` cleared (so a delayed reply can't land in a closed session).  
**Response (incorrect):** `{"correct": false, "hint": "Consider re-examining the patient's speech patterns"}` — session stays `active`, nothing persisted to scores.  
**Errors:** 403 not a student, 404 session not found / not owner, 409 session not active, 422 invalid body (empty primary_dx, >3 differentials, justification <50 chars), 502 LLM failure  
`ScoreOut` = primary_dx, differentials, justification, is_correct, rubric_score, response_time_score, total_score, feedback_text, graded_at.  
**Note:** `GET /api/v1/sessions/{id}` now includes `score` (ScoreOut) and `reveal` (disease_name, dsm_code, unit_label) for `diagnosed` sessions, and `null` for both while `active`.

### GET /api/v1/sessions
**Role required:** student or professor  
**Query:** `course_id` (required), `status` (optional `SessionStatus`), `student_id` (optional, professor only), `page` (default 1), `page_size` (default 20, max 100)  
**Response (200):** `{ "items": [CompletedSessionItem], "total": N, "page": p, "page_size": s }`. Students see only their own sessions; professors must own the course (else 404) and may filter by `student_id`. Ordered by `completed_at` desc (nulls last). `CompletedSessionItem` = session_id, disease_name, category, score (nullable), turn_count, started_at, completed_at, avg_response_latency_sec.

---

## Analytics

| Method | Path | Description | Role | Status |
|--------|------|-------------|------|--------|
| GET | `/api/v1/analytics/student/summary?course_id={id}` | Student's own scores, trends, weak categories | student | ✅ Week 13 |
| GET | `/api/v1/analytics/professor/class-summary?course_id={id}` | Class overview stats | professor | ✅ Week 14 |
| GET | `/api/v1/analytics/professor/student/{user_id}?course_id={id}` | Per-student drill-down | professor | ✅ Week 14 |
| GET | `/api/v1/analytics/professor/export?course_id={id}` | CSV grade export | professor | ✅ Week 14 |

### GET /api/v1/analytics/student/summary
**Role required:** student  
**Query:** `course_id` (required)  
**Response (200):** `StudentSummary` = total_cases, completed_cases, avg_score (nullable), avg_response_time_sec (nullable), scores_by_case[], scores_by_category{}, response_time_trend[], weak_categories[] (avg category score < 60). Redis-cached 300s; invalidated on new score.

### GET /api/v1/analytics/professor/class-summary
**Role required:** professor (must own course, else 404)  
**Query:** `course_id` (required), `bottom_pct` (optional, default `0.2`, `0 < pct ≤ 1`) — fraction flagged as bottom performers  
**Response (200):** `ClassSummary`:
- `enrolled_students`, `students_with_active_case`, `total_completed_cases`, `avg_class_score` (nullable)
- `completion_by_unit[]` — `{unit_label, total_diseases, total_cases_started, total_diagnosed, avg_score}` per unit
- `score_distribution[]` — per completed case, 5 buckets `0-20 / 21-40 / 41-60 / 61-80 / 81-100`
- `category_heatmap` — `{students[] (emails, ≥1 completed case), categories[], scores[][] (avg or null)}`
- `flagged_students[]` — bottom `bottom_pct` by avg score: `{email, avg_score, completed_cases}`, worst first

Redis-cached 300s (default `bottom_pct` only); invalidated on new score.

### GET /api/v1/analytics/professor/student/{user_id}
**Role required:** professor (must own course, else 404; student must be enrolled, else 404)  
**Query:** `course_id` (required), `page` (default 1), `page_size` (default 100, max 100)  
**Response (200):** `StudentDrilldown` = all `StudentSummary` fields **+** `sessions[]` (`CompletedSessionItem`, any status) **+** `total`. Each session's `session_id` links to `GET /api/v1/sessions/{session_id}` for the transcript.

### GET /api/v1/analytics/professor/export
**Role required:** professor (must own course, else 404)  
**Query:** `course_id` (required), `format` (default `csv`; any other value → 400)  
**Response (200):** `text/csv` with `Content-Disposition: attachment; filename="grades_{course_id}.csv"`. One row per diagnosed case across all students, ordered by `student_email` then per-student completion. Columns: `student_email, student_name, case_number, disease_name, category, score, response_time_avg, turns, date_completed` (`case_number` = per-student 1-based ordinal by completion).

---

## Health

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| GET | `/health` | Service health check | ✅ Done |

---

## JWT Payload

```json
{
  "sub": "<user UUID>",
  "email": "user@rutgers.edu",
  "role": "student" | "professor" | null,
  "iat": <unix timestamp>,
  "exp": <unix timestamp>
}
```

Algorithm: RS256. Access token TTL: 15 minutes. Refresh token TTL: 7 days (single-use, rotated on every refresh).
