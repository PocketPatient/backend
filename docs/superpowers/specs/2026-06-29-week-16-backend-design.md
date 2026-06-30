# Week 16 Backend — API Documentation & Database Cleanup

**Date:** 2026-06-29
**Scope:** Week 16 Tasks 1 (API documentation) and 2 (Database cleanup).
Task 3 (production deployment) is deferred. Anything touching live infra
(DB backup) is **authored only** — the user runs it.

---

## Task 1 — API Documentation

### Goal
Make the auto-generated OpenAPI spec (`/openapi.json`, `/docs`) complete and
accurate: every route has a human summary, error responses are documented, and
the core request/response models carry descriptions + examples. Add a written
API guide. Lock it in with a completeness test so docs don't rot.

### Current state
- 30 endpoints across 8 routers; FastAPI auto-generates the schema.
- No route `summary`s, no per-route `responses`, no schema `Field` descriptions
  or examples, minimal `FastAPI(...)` metadata.
- `app/main.py` already defines a standard error envelope (`{detail, code}`)
  and a `_STATUS_TO_CODE` map.

### Changes

1. **App metadata** (`app/main.py`)
   - Expand `FastAPI(...)` with a `description` (project summary + auth note),
     `contact`, `license_info`, and `openapi_tags` (`tags_metadata`) — one entry
     per router domain: `auth`, `users`, `courses`, `units`,
     `disease-documents`, `enrollments`, `sessions`, `analytics`.

2. **Shared error responses** (`app/openapi.py`, new)
   - `ERROR_RESPONSES`: a dict keyed by status code (401, 403, 404, 422, 429)
     mapping to OpenAPI `responses` entries that document the `{detail, code}`
     envelope, with the `code` value from `_STATUS_TO_CODE`.
   - Helper `errors(*codes)` returning the subset for a route's `responses=`.

3. **Route docs** (all routers)
   - Add `summary=` (one line) and `tags=[...]` to all 30 routes.
   - Attach the relevant `errors(...)` subset via `responses=`.

4. **Schema docs** (`app/schemas/*`) — *core models + examples* depth
   - Add `model_config`/`json_schema_extra` with **one example** to each core
     request and response model that appears in OpenAPI components.
   - Add `description=` on `Field(...)` for non-obvious fields only (IDs,
     timestamps, enums, computed fields like `student_count`). Do not annotate
     every field.

5. **Completeness test** (`tests/test_openapi_docs.py`, new)
   - Load `app.openapi()` and assert:
     - every path/operation has a non-empty `summary` and `tags`,
     - operations that can fail declare their error responses (spot-check a
       representative set, e.g. all auth-guarded routes declare 401),
     - the core schema components carry a `description`.
   - This is the TDD anchor: write it first (red), then add metadata (green).

6. **API guide** (`docs/api-guide.md`, new)
   - Auth flow: Google sign-in → backend issues RS256 JWT → `Authorization:
     Bearer`. Token verification in `app/deps.py`; roles via `require_role`.
   - Standard error envelope + the full `code` table.
   - Conventions: ownership checks return 404 (not 403) to avoid leaking
     existence; rate limiting (429); `/api/v1` prefix; `/health`.
   - Pointer to `/docs` (Swagger) and `docs/api-contract.md`.

### Testing
- `tests/test_openapi_docs.py` drives the work (TDD). Existing integration
  tests continue to pass unchanged (metadata is additive).

---

## Task 2 — Database Cleanup

### Goal
Add missing indexes, tighten one cascade and one invariant, and provide
retention + backup tooling — without changing query behavior.

### Changes

1. **Missing indexes** (model `__table_args__` + migration)
   Postgres does not auto-index FK columns. Add indexes on the unindexed,
   queried FK columns:
   - `courses.professor_id` → `ix_courses_professor_id` (list courses by prof)
   - `units.course_id` → `ix_units_course_id` (list units in a course)
   - `sessions.disease_id` → `ix_sessions_disease_id`
   - `disease_documents.uploaded_by` → `ix_disease_documents_uploaded_by`
   Already covered (no change): `enrollments` (composite unique leads with
   `user_id`; `course_id` indexed), `messages.session_id` (composite index),
   `scores.session_id` (unique), `sessions.user_id`/`course_id` (composite
   indexes), `diseases.unit_id` (indexed).

2. **Cascading delete** — minimal, history-safe
   - `messages.session_id` → `ondelete="CASCADE"` (deleting a session removes
     its messages; `scores.session_id` already cascades).
   - **Intentionally left RESTRICT** (documented in code comments): the FKs on
     `courses.professor_id`, `enrollments.*`, `sessions.*`, and
     `disease_documents.uploaded_by`. A course/user with history cannot be
     deleted out from under student records by accident; deletion is guarded at
     the application layer instead.

3. **Check constraint** — quiet-hours pairing
   - Enums are already native PG enum types, so they are type-enforced; no
     extra enum CHECK is added (documented).
   - Add `ck_quiet_hours_paired` on `users`: both `quiet_hours_start` and
     `quiet_hours_end` set, or both NULL. This codifies the invariant already
     described in the model comment.

4. **Retention script** (`scripts/archive_old_sessions.py`, new)
   - Selects sessions whose `started_at` is older than 3 years, with their
     messages and scores.
   - Exports each batch to a timestamped JSONL file under an `archives/`
     directory (gitignored).
   - **Dry-run by default** (reports counts only). `--apply` performs the
     export and then deletes the sessions (messages cascade via the new FK;
     scores cascade already). `--years N` overrides the threshold.
   - Pure-ish: DB access via the app's async session; archive-selection logic
     factored into a testable helper.

5. **Backup script** (`scripts/backup_db.sh`, new) — authored only
   - `pg_dump` wrapper reading the DB connection from `.env`/`$DATABASE_URL`,
     writing a timestamped dump to `backups/`. Includes the documented restore
     command (`pg_restore` / `psql`) in a header comment.
   - The user runs it before Phase 4; not executed in this session.

6. **Migration**
   - One alembic revision via `--autogenerate`, then hand-verify the
     `ondelete` and CHECK clauses (autogenerate is unreliable for these per
     CLAUDE.md). Indexes + the `messages` FK change + the users CHECK.

### Testing (TDD)
The test DB builds from `Base.metadata`, so model-level indexes/constraints are
present there.
- **Cascade:** create a session with messages + score, delete the session,
  assert messages and score are gone (new `messages` cascade; scores already).
- **RESTRICT guards:** deleting a course that has sessions raises an
  `IntegrityError` (confirms history is protected).
- **Quiet-hours check:** inserting a user with only one of start/end raises
  `IntegrityError`; both-null and both-set succeed.
- **Indexes:** `inspect(engine)` reports the four new index names on their
  tables.
- **Archiver:** seed old + recent sessions; the selection helper returns only
  the old ones; `--apply` removes old sessions and their messages/scores while
  leaving recent ones intact.

---

## Out of scope
- Task 3 (Dockerfile multi-stage, `cloudbuild.yaml`, dev/prod configs, Cloud
  Run deploy) — deferred to a later session.
- Actually running `backup_db.sh` or `archive_old_sessions.py --apply` against
  the live dev DB — the user runs those.
