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

### GET /api/v1/users/me
**Response:** `UserOut` — id, google_uid, email, role, is_verified, display_name, created_at  
**Errors:** 401 missing/invalid token

### PUT /api/v1/users/me/role
**Request:** `{"role": "student" | "professor"}`  
**Response:** updated `UserOut`  
**Errors:** 401 unauthenticated, 409 role already set, 422 invalid role value  
**Side effects:** student → `is_verified=true`; professor → `is_verified=false` (pending approval)

---

## Courses

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| GET | `/api/v1/courses` | List courses for current user | Bearer JWT | TBD |
| POST | `/api/v1/courses` | Create a course (professor only) | Bearer JWT | TBD |
| GET | `/api/v1/courses/{id}` | Get course details | Bearer JWT | TBD |
| POST | `/api/v1/courses/{id}/enroll` | Enroll with class code | Bearer JWT | TBD |

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
