# Week 3 Backend Design — Course CRUD & Enrollment

**Date:** 2026-05-19
**Scope:** Backend tasks from `week-03.md` — course endpoints, enrollment endpoints, integration tests

---

## 1. Data Models

Both SQLAlchemy models are already defined. No new Alembic migrations are needed.

- `app/models/course.py` — `Course` table: `id`, `title`, `professor_id`, `class_code`, `semester`, `is_active`, `msg_window_start`, `msg_window_end`, `msg_timezone`, `created_at`
- `app/models/enrollment.py` — `Enrollment` table: `id`, `user_id`, `course_id`, `enrolled_at`. Has `UniqueConstraint("user_id", "course_id")`.

---

## 2. Schemas

New files in `app/schemas/`:

### `app/schemas/course.py`
- `CourseCreate` — `title: str`, `semester: str | None`
- `CourseUpdate` — all optional: `title`, `semester`, `msg_window_start`, `msg_window_end`, `msg_timezone`
- `CourseOut` — `id`, `title`, `professor_id`, `class_code`, `semester`, `is_active`, `msg_window_start`, `msg_window_end`, `msg_timezone`, `created_at`, `student_count: int`
  - `student_count` is computed via `COUNT(enrollments)` — not a stored column.

### `app/schemas/enrollment.py`
- `EnrollmentJoinRequest` — `class_code: str`
- `EnrolledStudentOut` — `user_id: UUID`, `email: str`, `display_name: str | None`, `enrolled_at: datetime`

---

## 3. Endpoints

### `app/routers/courses.py` — prefix `/courses`, tag `courses`

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/courses` | professor only | Creates course, auto-generates `class_code` |
| `GET` | `/courses` | authenticated | Professors see owned; students see enrolled |
| `GET` | `/courses/{course_id}` | authenticated | Must be owner or enrolled; returns 404 otherwise |
| `PUT` | `/courses/{course_id}` | professor owner | Updates title, semester, msg window fields |
| `DELETE` | `/courses/{course_id}/deactivate` | professor owner | Sets `is_active = false` |

### `app/routers/enrollments.py` — tag `enrollments`

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/enrollments/join` | student only | Joins via `class_code` |
| `GET` | `/courses/{course_id}/students` | professor owner | Lists enrolled students with `enrolled_at` |
| `DELETE` | `/courses/{course_id}/students/{user_id}` | professor owner | Removes a student |

The `/courses/{course_id}/students` endpoints are defined in `enrollments.py` but registered on the app without a prefix (since they share the `/courses` path).

### Class Code Generation
- 6-character uppercase alphanumeric
- Excludes ambiguous characters: `0`, `O`, `1`, `I`, `L`
- Collision retry loop: up to 10 attempts, raises 500 if all collide

---

## 4. Error Handling

| Scenario | Status Code |
|----------|-------------|
| Student calls `POST /courses` | 403 |
| Professor calls `POST /enrollments/join` | 403 |
| Course not found or user not owner/enrolled | 404 (don't leak existence) |
| Join with non-existent class code | 404 |
| Student already enrolled | 409 |
| Course is inactive (`is_active = false`) | 410 |
| Class code collision exhausted after 10 retries | 500 |

Authorization:
- Role enforcement via existing `require_role("professor")` / `require_role("student")` from `app/deps.py`
- Ownership checks: fetch course, compare `professor_id == current_user.id`, return 404 on mismatch

---

## 5. Student Count

`CourseOut.student_count` is computed at query time using a correlated `COUNT` subquery or joined aggregate on the `enrollments` table. Applied to all endpoints that return `CourseOut` (list and detail).

---

## 6. Testing

### Test Database Setup (`tests/conftest.py`)
- New async engine pointed at `pocketpatient_test` (overridable via `TEST_DATABASE_URL` env var)
- `Base.metadata.create_all` at session start, `drop_all` at session end
- Overrides `get_db` app dependency to use test session
- Stubs Firebase (no real token verification) and Redis (no real connection)
- Creates two fixture users: one professor, one student
- Issues real JWT tokens using a test RSA keypair (matching existing `conftest.py` pattern)
- Uses `httpx.AsyncClient` with `ASGITransport` for async integration tests

### Test Files
- `tests/test_courses_router.py`
- `tests/test_enrollments_router.py`

### Test Cases
- Professor creates course → 201, response includes `class_code` and `student_count: 0`
- Student joins with valid code → 200, returns course details
- Student joins with invalid code → 404
- Student joins already-enrolled course → 409
- Student joins inactive course → 410
- Student tries to create course → 403
- Professor sees enrolled students → list with `enrolled_at`
- Professor deactivates course → `is_active: false`
- `GET /courses` professor sees owned courses; student sees enrolled courses
- Professor updates course fields → 200 with updated values
- Professor removes student from course → 204

---

## 7. Registration

Add to `app/main.py`:
```python
from app.routers import courses, enrollments
app.include_router(courses.router, prefix="/api/v1")
app.include_router(enrollments.router, prefix="/api/v1")
```
