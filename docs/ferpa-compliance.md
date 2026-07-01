# FERPA Compliance Measures

PocketPatient handles student education records (session transcripts, scores).
This documents the controls that keep that data access-controlled and minimized.

## Role-based access control
- Every protected route requires a valid RS256 JWT (`app/deps.py: get_current_user`).
- Role gates via `require_role("professor" | "student")`.
- Professors can only access courses they own (`Course.professor_id == current_user.id`)
  and, transitively, only the units/sessions/scores under those courses.
- Students can only access their own sessions (`Session.user_id == current_user.id`).

## Existence hiding (404, not 403)
Ownership failures return **404 Not Found**, never 403, so a caller cannot tell
whether a resource they don't own exists. Verified by `tests/test_ferpa_rbac.py`.

## LLM grading de-identification
Transcripts sent to Gemini for grading are built by
`grading_service._build_transcript`, which labels every turn as generic
"Student:" / "Patient:" — no names, emails, or user IDs are included.
Verified by `test_transcript_contains_no_student_pii`.

## PII minimization in API responses
- Session/score responses expose only pedagogical fields (diagnosis, rubric, feedback).
- No student email/display name is embedded in another user's data views.

## Authentication token handling
- Access tokens: short-lived (15 min) RS256 JWTs carrying a `jti` for traceability.
- Refresh tokens: only SHA-256 **hashes** are stored (in Redis), single-use with
  rotation; `POST /api/v1/auth/logout` revokes all of a user's refresh tokens.
- No cookies are used (bearer-header auth), so cookie security flags are N/A.

## Rate limiting
Per-endpoint limits (auth 10/min, messages 30/min, analytics 60/min, others
100/min) mitigate scraping and brute-force against these records.
