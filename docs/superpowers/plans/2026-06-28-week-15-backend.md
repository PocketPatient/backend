# Week 15 Backend — Analytics Polish, Edge Cases & Notification Preferences

**Goal:** Tighten the analytics layer (cache TTLs, indexes, profiling), lock in
analytics edge-case behavior, and add user notification preferences with
quiet-hours-aware push delivery.

**Status:** ✅ Implemented (TDD). Full suite: 357 passed.

---

## Task 1 — Analytics caching + performance

- **Cache TTLs split** (`app/services/analytics_cache.py`):
  `STUDENT_SUMMARY_TTL_SEC = 120` (individual student) and
  `CLASS_SUMMARY_TTL_SEC = 300` (class summary). Router now passes the right TTL
  for each path (student summary + drill-down → 120s; class summary → 300s).
- **Invalidation** on new score: already wired — `diagnose` deletes both
  `summary_key(user, course)` and `class_summary_key(course)`. Covered by
  `test_summary_cache_invalidation.py`.
- **Indexes** (models + migration `7892262555f3`):
  - `ix_sessions_course_id_status` (course_id, status) — class aggregations
  - `ix_sessions_user_id_course_id` (user_id, course_id) — student scope
  - `ix_enrollments_course_id` (course_id) — class enrollment counts/joins
  - `ix_diseases_unit_id` (unit_id) — completion-by-unit join
  Guarded by `tests/test_analytics_indexes.py`.
- **Profiling** (`scripts/profile_analytics.py`): seeds 200 students × 12 cases
  (2400 diagnosed sessions) and times the cold/uncached aggregations.
  Measured: class_summary ≈ 25–32 ms, student_summary ≈ 2 ms — well under the
  500 ms target (cached responses are O(1) Redis reads).

## Task 2 — Edge cases in analytics

Verified + locked in via `tests/test_analytics_edge_cases.py` (existing logic
already handled all five correctly):
1. Student with 0 completed cases → empty summary, no division-by-zero.
2. Student in multiple courses → analytics scoped per `course_id`.
3. Professor course with 0 completed cases → zeros/None, empty heatmap.
4. Disease never assigned → absent from category breakdown / heatmap.
5. Score of 0 → counted in averages (not dropped/None), weak-category logic intact.

## Task 3 — Notification preferences

- **Model** (`app/models/user.py`): `push_enabled` (bool, default true),
  `quiet_hours_start` / `quiet_hours_end` (`Time`, nullable, both-or-neither).
- **Endpoint** `PUT /api/v1/users/me/notification-preferences`
  (`app/routers/users.py`): validates both-or-neither quiet hours (422 otherwise),
  persists, returns saved prefs. Documented in `docs/api-contract.md`.
- **Quiet-hours helpers** (`app/services/push_service.py`):
  `is_within_quiet_hours(now, start, end)` (half-open window, wraps midnight) and
  `next_window_open(now, end)`. Unit-tested in `tests/test_quiet_hours.py`.
- **Push delivery** (`app/tasks/push_notifications.py`): `_get_push_state` loads
  token + prefs in one query; `send_push` drops when disabled, and when inside the
  quiet window re-enqueues itself with `eta = next_window_open(...)` so the push
  fires when quiet hours close instead of being lost. Covered by
  `tests/test_push_quiet_hours.py` and updated `tests/test_push_task.py`.

## Migration

`alembic/versions/7892262555f3_week15_notification_prefs_analytics_.py` — adds the
three user columns + four indexes. Down/up round-trip verified.
