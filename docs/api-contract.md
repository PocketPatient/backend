# API Contract

Base URL (local dev): `http://localhost:8000/api/v1`

---

## Auth

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| GET | `/api/v1/auth/cas/login` | Redirect to Rutgers CAS | Week 2 |
| GET | `/api/v1/auth/cas/callback` | CAS callback, returns JWT | Week 2 |
| POST | `/api/v1/auth/logout` | Invalidate session | Week 2 |

## Users

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| GET | `/api/v1/users/me` | Get current user profile | Week 2 |

## Courses

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| GET | `/api/v1/courses` | List courses for current user | TBD |
| POST | `/api/v1/courses` | Create a course (professor only) | TBD |
| GET | `/api/v1/courses/{id}` | Get course details | TBD |
| POST | `/api/v1/courses/{id}/enroll` | Enroll with class code | TBD |

## Health

| Method | Path | Description | Status |
|--------|------|-------------|--------|
| GET | `/health` | Service health check | ✅ Done |
