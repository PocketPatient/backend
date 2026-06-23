# Week 13 Backend — Student Analytics Design

**Date:** 2026-06-22
**Phase:** 3 — Analytics
**Theme:** Student dashboard — scores + trends
**Source:** `week-13.md` (Dev A / Backend tasks 1–3)

## Goal

Expose per-student analytics for the student dashboard: a summary endpoint
(scores, trends, weak categories) and a paginated list of completed sessions.
Aggregations are computed in SQL and cached in Redis. The aggregation query
helpers are written to be reusable by next week's professor dashboard.

## Locked decisions

- `total_cases` = number of sessions the student has **started** for the course
  (any status: active + diagnosed + abandoned). `completed_cases` = sessions
  with status `diagnosed`.
- Aggregations are implemented with **SQL CTEs / aggregation queries** inside
  reusable service helpers. **No Postgres materialized views / migrations.**
  Redis is the staleness/refresh layer (5-min TTL, invalidated on new score).
- The sessions list endpoint takes a generic `status` query param (any value),
  not a diagnosed-only restriction.
- Weak-category threshold: average category score **< 60** (matches the
  frontend "below 60 → red" rule).

## Data model (existing, no changes)

- `Session(id, disease_id, user_id, course_id, started_at, completed_at,
  status, turn_count, avg_response_latency_sec)`. `status` ∈
  {`active`, `diagnosed`, `abandoned`}.
- `Score(session_id UNIQUE, total_score, rubric_score, response_time_score,
  graded_at, ...)`. `total_score` is on a 0–100 scale.
- `Disease(id, unit_id, name, category, ...)`.
- A `diagnosed` session normally has exactly one `Score` row.

## Components

### New files
- `app/schemas/analytics.py` — Pydantic response models.
- `app/services/analytics_cache.py` — JSON get/set/invalidate over
  `app.state.redis`.
- `app/routers/analytics.py` — `/analytics` router, registered in
  `app/main.py` with the `/api/v1` prefix.
- `tests/test_analytics.py` — endpoint + math tests.

### Changed files
- `app/services/analytics_service.py` — add `get_student_summary(...)`,
  `list_sessions(...)`, and the reusable aggregation helpers.
- `app/routers/sessions.py` — add `GET /sessions` list endpoint and invalidate
  the summary cache when a Score is committed in `diagnose`.
- `app/main.py` — register the analytics router.

## Task 1 — `GET /api/v1/analytics/student/summary?course_id={id}`

Student-only (`require_role("student")`). Computes over `user_id` +
`course_id`. Response shape (matches `week-13.md`):

```json
{
  "total_cases": 8,
  "completed_cases": 6,
  "avg_score": 72.5,
  "avg_response_time_sec": 1800,
  "scores_by_case": [
    {"session_id": "...", "disease_name": "MDD", "category": "Mood Disorders",
     "score": 85, "completed_at": "2026-08-15T..."}
  ],
  "scores_by_category": {
    "Mood Disorders": {"avg_score": 80, "count": 3}
  },
  "response_time_trend": [
    {"case_number": 1, "avg_latency_sec": 3600}
  ],
  "weak_categories": ["Psychotic Disorders"]
}
```

### Computation (SQL aggregations, not Python)

Three queries scoped to `user_id` + `course_id`:

1. **Counts / overall averages** — conditional aggregation:
   - `total_cases` = `count(*)` over all sessions.
   - `completed_cases` = `count(*) filter (where status = 'diagnosed')`.
   - `avg_score` = `avg(scores.total_score)` over diagnosed sessions.
   - `avg_response_time_sec` = `avg(sessions.avg_response_latency_sec)` over
     diagnosed sessions.

2. **Per-case rows** — `sessions ⋈ diseases ⋈ scores`, `status = 'diagnosed'`,
   ordered by `completed_at` asc. Yields, per session: `session_id`,
   `disease_name`, `category`, `score` (`total_score`), `completed_at`,
   `avg_response_latency_sec`. Drives:
   - `scores_by_case` (the rows as-is).
   - `response_time_trend` — `case_number` is the 1-based ordinal in this
     ordering; `avg_latency_sec` = the session's `avg_response_latency_sec`.

3. **Per-category aggregation** — group diagnosed sessions by
   `disease.category`: `avg(total_score)` and `count(*)`. Drives:
   - `scores_by_category` (map of category → `{avg_score, count}`).
   - `weak_categories` — categories whose `avg_score < 60`.

### Caching
- Key: `analytics:summary:{user_id}:{course_id}`.
- On read: `cached = await redis.get(key)`; return parsed JSON only when
  `isinstance(cached, str)` (so the test `AsyncMock` and missing-redis both fall
  through to a fresh compute). Otherwise compute, then `set` with TTL 300s.
- Graceful degradation: if `redis` is `None` or any redis call raises, compute
  fresh and skip caching.
- Invalidation: in `sessions.diagnose`, after a Score is committed, delete the
  key for that `user_id`+`course_id`.

### Edge cases
- No sessions: `total_cases = 0`, `completed_cases = 0`, averages `null`,
  lists/maps empty, `weak_categories = []`.
- Diagnosed session missing its Score row (defensive): excluded from
  score-based aggregations (inner join on scores), still counted in
  `total_cases`.

## Task 2 — `GET /api/v1/sessions` (list)

Query params: `course_id` (required), `status` (optional, any
`SessionStatus`), `student_id` (optional, professor only), `page` (default 1),
`page_size` (default 20, max 100).

- **Student:** returns only their own sessions; `student_id` is ignored.
- **Professor:** must own the course (else 404, to avoid leaking existence);
  may filter by `student_id`.
- Ordered by `completed_at` desc (nulls last), then `started_at` desc.
- Response: `{ "items": [...], "total": N, "page": p, "page_size": s }`.
- Each item: `session_id`, `disease_name`, `category`, `score` (`total_score`,
  nullable), `turn_count`, `started_at`, `completed_at`,
  `avg_response_latency_sec`.
- `total` via a `count(*)` query with the same filters.

Routing note: lives at `GET /sessions` (empty path on the existing router);
no conflict with `GET /sessions/{session_id}` or `GET /sessions/active`.

## Task 3 — Reusable aggregation helpers

Satisfied by structuring the Task 1 SQL into reusable functions in
`analytics_service` (logical equivalents of the requested
`student_category_scores`, `course_completion_stats`,
`student_response_trends`), rather than DB materialized views. Next week's
professor dashboard reuses these helpers; Redis provides the refresh/staleness
strategy.

## Testing (TDD)

`tests/test_analytics.py` and a sessions-list test, using the existing
`clean_tables`, `professor`, `student` fixtures:

- Summary math validated against seeded raw rows (avg score, per-category
  averages, response-time trend ordering/ordinals).
- Weak-category threshold boundary (avg exactly 60 not weak; < 60 weak).
- Empty-data case (zero sessions).
- Student scoping: a student sees only their own data.
- Role enforcement: professor blocked from the student summary endpoint (403).
- Sessions list: pagination (`total`, `page`, `page_size`, slicing), status
  filter, student-sees-own-only, professor course-ownership 404, professor
  `student_id` filter.

## Out of scope

- Professor dashboard endpoints (next week).
- Postgres materialized views.
- Streak computation (frontend-only per `week-13.md`).
