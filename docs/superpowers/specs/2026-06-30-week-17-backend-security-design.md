# Week 17 — Backend Security Hardening — Design

**Date:** 2026-06-30
**Source:** `week-17.md` (Dev A / Backend, Phase 4: Polish & Launch — Security hardening)
**Status:** Approved

## Overview

Three cohesive backend security workstreams, delivered as one spec + one plan:

1. Input sanitization + tiered rate limiting
2. FERPA compliance verification + documentation
3. JWT hardening (jti claim, per-user refresh-token index, logout revocation)

TDD throughout: failing test → minimal impl → green → commit.

## Current state (audited before design)

- `RateLimitMiddleware` exists with only two tiers: auth (20/min, by IP) and standard (100/min, by user/IP). Redis INCR+EXPIRE pipeline, fail-open when Redis is unavailable.
- Refresh tokens are **already** single-use with rotation (`getdel`) and stored as SHA-256 **hashes in Redis** (`refresh:{hash}` → user_id, 7-day TTL). No per-user index, so "revoke all" is not yet possible.
- Access tokens (`create_access_token`) carry `sub, email, role, iat, exp` (15-min expiry). No `jti`.
- Auth is `Authorization: Bearer` — **no cookies**, so "secure cookie flags" is N/A.
- Ownership checks across routers already return **404** (not 403) to avoid leaking existence.
- `grading_service._build_transcript` already labels turns generically ("Student"/"Patient") — no names/emails sent to Gemini.
- `MessageCreate.content` has `min_length=1` but no max; `DiagnosisCreate` fields are already length-capped.

## Decisions (locked)

- **Refresh token storage:** stay in Redis; add a per-user index set for logout-all. (No Postgres table / migration.)
- **Access-token revocation:** `jti` claim for traceability only; rely on 15-min natural expiry. No per-request denylist.
- **Sanitization:** stdlib-only helper (no new dependency). Frontend is Flutter (no HTML rendering), so this is defense-in-depth + length enforcement, not XSS-critical.

## Task 1 — Input sanitization + rate limiting

### Sanitization

New `app/services/sanitize.py`:

- `strip_tags(s: str) -> str` — uses `html.parser.HTMLParser` to drop tags (robust against nesting; avoids regex foot-guns), then collapses runs of whitespace and trims.
- `sanitize_text(s: str, max_len: int) -> str` — `strip_tags` + trim; raises `ValueError` if the result exceeds `max_len` (surfaces as Pydantic 422).

Applied via Pydantic v2 field validators on request schemas:

- `MessageCreate.content` — sanitize, cap **2000** chars.
- `DiagnosisCreate.primary_dx`, `justification`, `differentials[]` — sanitize (keep existing length caps).
- `CourseCreate.name`, `UnitCreate.label` — sanitize + reasonable cap (defense-in-depth for professor inputs).

Over-length / invalid → 422 via the existing `{detail, code: "VALIDATION_ERROR"}` envelope.

### Rate limiting

Extend `RateLimitMiddleware` from 2 to 4 tiers:

| Tier | Match | Limit | Key |
|---|---|---|---|
| auth | path startswith `/api/v1/auth` | 10/min | client IP |
| message | `POST` and path matches `/api/v1/sessions/{id}/messages` | 30/min | user id |
| analytics | path startswith `/api/v1/analytics` | 60/min | user id |
| other | everything else | 100/min | user id (IP fallback) |

- Same Redis INCR+EXPIRE pipeline and fail-open behavior.
- Message-path match via a compiled regex on `path` + method check.
- Distinct Redis key namespaces per tier so limits don't collide (`rl:auth:`, `rl:msg:`, `rl:analytics:`, `rl:std:`).
- Lowering auth 20→10 requires updating the existing `test_rate_limit_auth_endpoint`.

## Task 2 — FERPA compliance

### Verification tests — `tests/test_ferpa_rbac.py`

- Professor A cannot read professor B's course → 404.
- Professor A cannot list/read sessions in professor B's course → 404.
- Student A cannot read student B's session → 404.
- Transcript sent to Gemini contains no student name/email — assert `_build_transcript` output uses only generic speaker labels.

### `docs/ferpa-compliance.md`

Documents: RBAC model (role deps + course-ownership checks), 404-not-403 existence hiding, transcript de-identification for LLM grading, review of API responses for PII leakage, refresh-token handling (hashed, single-use, revocable), and the N/A cookie-flags note.

## Task 3 — JWT hardening

- **`jti`:** add `jti = str(uuid.uuid4())` to the access-token payload in `create_access_token` (traceability; no denylist).
- **Per-user refresh index:** maintain a Redis set `refresh_user:{uid}` of that user's active token hashes.
  - `create_refresh_token`: SETEX `refresh:{hash}` + SADD `refresh_user:{uid}` + (re)set the set's TTL.
  - `verify_and_rotate_refresh_token`: on rotation, SREM the old hash and SADD the new one (old key already removed by `getdel`).
- **`POST /api/v1/auth/logout`** (requires `get_current_user`): SMEMBERS `refresh_user:{uid}` → DEL each `refresh:{hash}`, then DEL the set. Idempotent. Returns **204 No Content**.
- Cookie flags: N/A — noted in the compliance doc.

## Testing summary

- `tests/test_sanitize.py` — unit tests for `strip_tags` / `sanitize_text` and schema validators.
- `tests/test_middleware.py` — new tier tests (message 30, analytics 60, other 100); update auth to 10.
- `tests/test_ferpa_rbac.py` — cross-tenant RBAC + transcript de-id.
- `tests/test_auth_router.py` / `tests/test_auth_service.py` — logout revokes all refresh tokens; rotation maintains the per-user index; `jti` present in access tokens.

## Out of scope (YAGNI)

- Postgres refresh-token table / migration.
- Access-token denylist / per-request revocation lookup.
- New sanitization dependency (nh3/bleach).
- Cookie-based auth.
