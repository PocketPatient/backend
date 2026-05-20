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
