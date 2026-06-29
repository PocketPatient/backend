# Week 14 Backend — Professor Analytics Design

**Date:** 2026-06-28
**Phase:** 3 — Analytics
**Theme:** Professor dashboard — class overview
**Source:** `week-14.md` (Dev A / Backend tasks 1–3)

## Goal

Expose class-level analytics for the professor dashboard: a class-summary
endpoint (aggregate stats, per-unit completion, score distribution, category
heatmap, flagged students), an individual student drill-down, and a CSV grade
export. Builds directly on the week-13 student-analytics layer — reuses
`get_student_summary`, `list_completed_sessions`, the `analytics` router, and
the Redis cache seam. Aggregations are computed in SQL inside reusable service
helpers. No Postgres materialized views / migrations.

## Locked decisions

- **Auth:** all three endpoints are `require_role("professor")` and verify the
  professor owns the course (`select(Course).where(id==course_id,
  professor_id==me)`). Missing/unowned course → **404** (leak avoidance,
  matching the existing pattern in `sessions.py`).
- **`score_distribution`** counts **per completed case** (one data point per
  diagnosed session's `total_score`). Always returns all 5 buckets, zero-filled.
- **`flagged_students`** = **bottom 20% percentile** by average score among
  students with ≥1 completed case. `k = ceil(n * bottom_pct)`. `bottom_pct` is a
  query param (default `0.2`, validated `0 < pct ≤ 1`).
- **`category_heatmap`** rows = enrolled students with ≥1 completed case (by
  email, sorted); columns = categories with ≥1 completed case (sorted); empty
  cells (student has no completed case in that category) are **null**.
- **CSV `format`** defaults to `csv`; any other value → **400**.
- **Score scale:** `Score.total_score` is 0–100. Averages rounded to 1 dp,
  `null` when no scored cases.

## Data model (existing, no changes)

- `Session(id, disease_id, user_id, course_id, started_at, completed_at,
  status, turn_count, avg_response_latency_sec)`, `status` ∈
  {`active`, `diagnosed`, `abandoned`}.
- `Score(session_id UNIQUE, total_score, ...)`, 0–100.
- `Disease(id, unit_id, name, category, ...)`.
- `Unit(id, course_id, label, created_at, ...)`.
- `Enrollment(user_id, course_id)`.
- `Course(id, professor_id, ...)`.
- `User(id, email, display_name, role)`.

## Task 1 — `GET /api/v1/analytics/professor/class-summary?course_id={id}&bottom_pct={0.2}`

Service helper `get_class_summary(course_id, db, bottom_pct=0.2) -> ClassSummary`.
Scoped to the course. Response shape (matches `week-14.md`):

```json
{
  "enrolled_students": 25,
  "students_with_active_case": 8,
  "total_completed_cases": 142,
  "avg_class_score": 68.3,
  "completion_by_unit": [
    {"unit_label": "Unit 1", "total_diseases": 3, "total_cases_started": 50,
     "total_diagnosed": 42, "avg_score": 71.2}
  ],
  "score_distribution": [
    {"range": "0-20", "count": 2}, {"range": "21-40", "count": 5},
    {"range": "41-60", "count": 12}, {"range": "61-80", "count": 18},
    {"range": "81-100", "count": 8}
  ],
  "category_heatmap": {
    "students": ["student1_email", "student2_email"],
    "categories": ["Anxiety", "Mood", "Psychotic"],
    "scores": [[80, 55, 90], [65, 70, null]]
  },
  "flagged_students": [
    {"email": "abc123@scarletmail.rutgers.edu", "avg_score": 35.2,
     "completed_cases": 4}
  ]
}
```

### Computation
- `enrolled_students` — `count(*)` of `enrollments` for the course.
- `students_with_active_case` — `count(distinct user_id)` of sessions with
  `status='active'` in the course.
- `total_completed_cases` — `count(*)` diagnosed sessions in the course.
- `avg_class_score` — `avg(scores.total_score)` over diagnosed sessions, 1 dp,
  `null` if none.
- `completion_by_unit` — one row per `Unit` in the course, ordered by
  `Unit.created_at, Unit.label`. Per unit:
  - `total_diseases` = `count(*)` diseases with `unit_id = unit.id`.
  - `total_cases_started` = `count(*)` sessions (any status) whose disease is in
    the unit.
  - `total_diagnosed` = `count(*)` of those with `status='diagnosed'`.
  - `avg_score` = `avg(total_score)` over diagnosed in the unit, 1 dp, `null` if
    none.
- `score_distribution` — bucket each diagnosed session's `total_score`:
  `[0,20]→"0-20"`, `(20,40]→"21-40"`, `(40,60]→"41-60"`, `(60,80]→"61-80"`,
  `(80,100]→"81-100"`. All 5 buckets always present, zero-filled.
- `category_heatmap` — per (student, category) avg of `total_score` over
  diagnosed sessions. `students` = sorted emails of enrolled students with ≥1
  completed case; `categories` = sorted distinct categories with ≥1 completed
  case; `scores[i][j]` = avg (1 dp) or `null`.
- `flagged_students` — among students with ≥1 completed case, rank ascending by
  avg score; take `k = ceil(n * bottom_pct)` worst. Each entry
  `{email, avg_score (1 dp), completed_cases}`, sorted worst-first. Empty when
  no students have completed cases.

### Caching
- Key `analytics:class:{course_id}`, TTL 300s, graceful degradation (compute
  fresh if redis is `None`/raises), same get/set pattern as the student summary.
- Invalidated in `sessions.diagnose` after a Score commits — alongside the
  existing per-student `analytics:summary:{user_id}:{course_id}` delete.

## Task 2 — `GET /api/v1/analytics/professor/student/{user_id}?course_id={id}`

- Course-ownership 404; plus the target `user_id` must be enrolled in the
  course (else 404).
- Reuses `get_student_summary(user_id, course_id)` (and the existing per-student
  cache) for the summary fields, and `list_completed_sessions(course_id,
  user_id)` for the session list (all statuses, ordered `completed_at` desc).
- Returns `StudentDrilldown` = all `StudentSummary` fields **+**
  `sessions: list[CompletedSessionItem]` **+** `total`. Each session item carries
  `session_id`; the frontend builds the transcript link via the existing
  `GET /api/v1/sessions/{session_id}`.
- `page`/`page_size` query params (default page 1, page_size 100, max 100) page
  the session list; the summary fields are unaffected.

## Task 3 — `GET /api/v1/analytics/professor/export?course_id={id}&format=csv`

- Course-ownership 404. `format` defaults to `csv`; other values → 400.
- Service helper `get_export_rows(course_id, db)` returns one record per
  diagnosed case across all students, ordered by `student_email`, then per-student
  completion order.
- Streams CSV via the `csv` module into a `Response` with
  `media_type="text/csv"` and
  `Content-Disposition: attachment; filename="grades_{course_id}.csv"`.
- Columns: `student_email, student_name, case_number, disease_name, category,
  score, response_time_avg, turns, date_completed`.
  - `case_number` = per-student 1-based ordinal by completion order.
  - `score` = `total_score`; `response_time_avg` = `avg_response_latency_sec`;
    `turns` = `turn_count`; `date_completed` = `completed_at` ISO-8601.

## Components

### New files
- `tests/test_professor_analytics.py` — endpoint + math tests (TDD).

### Changed files
- `app/schemas/analytics.py` — add `UnitCompletion`, `ScoreBucket`,
  `CategoryHeatmap`, `FlaggedStudent`, `ClassSummary`, `StudentDrilldown`.
- `app/services/analytics_service.py` — add `get_class_summary`,
  `get_export_rows`.
- `app/services/analytics_cache.py` — add `class_summary_key(course_id)`.
- `app/routers/analytics.py` — 3 professor endpoints + a course-ownership
  helper.
- `app/routers/sessions.py` — invalidate the class key in `diagnose`.
- `docs/api-contract.md` — document the 3 endpoints.

## Testing (TDD)

`tests/test_professor_analytics.py` using `clean_tables`, `professor`,
`student` fixtures (seed extra students/sessions/scores directly via
`db_session`):

- **class-summary:** enrolled/active counts; per-case avg; per-unit rows
  (started vs diagnosed vs avg); score-distribution bucketing incl. boundaries
  (20→"0-20", 21→"21-40", 100→"81-100"); heatmap rows/cols/null cells; flagged
  bottom-20% (`ceil` count, ordering, configurable `bottom_pct`); empty course.
- **auth:** student blocked (403); professor not owning course → 404.
- **drill-down:** matches `get_student_summary` math; session list present;
  unenrolled `user_id` → 404; not-owned course → 404.
- **export:** correct columns/rows, per-student `case_number`, headers
  (`Content-Disposition`, `text/csv`); `format=xml` → 400; not-owned → 404.
- **cache:** class-summary cache hit short-circuits compute; `diagnose`
  invalidates the class key.

## Out of scope

- Postgres materialized views.
- Frontend (Dev B).
- Anonymization of heatmap emails (frontend concern).
- Streaming/large-export pagination (full course CSV fits in memory).
