# PocketPatient — Backend

FastAPI backend for the Rutgers PocketPatient v2 clinical simulation platform. Handles authentication, course management, disease document parsing, and (in later phases) async chat, LLM integration, and grading.

**Stack:** Python 3.11 · FastAPI · SQLAlchemy 2.0 async · PostgreSQL · Redis · Firebase Admin SDK · RS256 JWT

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | Use pyenv or the official installer |
| Docker Desktop | Latest | Runs Postgres + Redis locally |
| Git | Any | |

---

## First-Time Setup

### 1. Clone the repo

```powershell
git clone <backend-repo-url>
cd backend
```

### 2. Create a virtual environment and install dependencies

```powershell
python -m venv .venv

# Windows
.venv\Scripts\activate

# Mac/Linux
source .venv/bin/activate

pip install uv
uv sync
```

### 3. Start Postgres and Redis via Docker

```powershell
docker compose up -d
```

This starts:
- PostgreSQL 16 on `localhost:5432` (db: `pocketpatient`, user: `postgres`, password: `postgres`)
- Redis 7 on `localhost:6379`

### 4. Configure environment variables

```powershell
cp .env.example .env
```

Open `.env` and fill in the following values:

```env
# Database (default works with docker-compose as-is)
database_url=postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient

# Redis (default works with docker-compose as-is)
redis_url=redis://localhost:6379/0

# Any string for local dev
secret_key=dev-secret-key-change-in-prod

# Firebase
firebase_project_id=pocket-patient-v2
firebase_credentials_path=serviceAccountKey.json

# Allow test accounts (@test.pocketpatient.dev domain) — local dev only, never in prod
allow_test_accounts=true

# JWT RS256 keys (see below for how to generate)
jwt_private_key="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
jwt_public_key="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
```

#### Generating JWT RS256 keys (one-time)

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out jwt_private.pem
openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem
```

Convert each to a single-line string for `.env`:

```bash
# Mac/Linux
awk 'NF {printf "%s\\n",$0;}' jwt_private.pem

# Windows PowerShell
(Get-Content jwt_private.pem) -join '\n'
```

Paste the output as the value of `jwt_private_key` and `jwt_public_key` in `.env`. The `.pem` files are gitignored — keep them locally or discard after copying.

### 5. Firebase service account key

The backend needs a Firebase service account key to verify Firebase ID tokens locally (on GCP Cloud Run it uses the default service account instead).

1. Go to [console.firebase.google.com](https://console.firebase.google.com) → select `pocket-patient-v2`
2. **Project Settings** → **Service accounts** tab
3. Click **Generate new private key** → **Generate key** → a `.json` file downloads
4. Rename it `serviceAccountKey.json` and place it in the root of this repo (next to `docker-compose.yml`)

The file is gitignored and must never be committed.

### 6. Run database migrations

```powershell
.venv\Scripts\activate   # if not already active
uv run alembic upgrade head
```

This creates all tables in the local Postgres instance.

### 7. Seed test accounts (optional but recommended for local dev)

```powershell
.venv\Scripts\activate
uv run python scripts/seed_test_users.py
```

This creates `student@test.pocketpatient.dev` and `professor@test.pocketpatient.dev` in both Firebase and PostgreSQL with roles pre-set. Requires `allow_test_accounts=true` in `.env`.

---

## Running the Server

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

- API: `http://localhost:8000/api/v1`
- Interactive docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health` → `{"status":"ok"}`

> **Why `.venv\Scripts\python.exe -m uvicorn` instead of `uv run uvicorn`?**
> On Windows with Conda/Miniconda as the base Python, `uv run uvicorn` and bare `uvicorn` invoke the
> `.venv\Scripts\uvicorn.exe` wrapper, which hard-codes the Conda base interpreter rather than the
> venv's isolated interpreter. That interpreter doesn't have `app/` in `sys.path[0]`, so it silently
> loads stale cached modules instead of the current source tree — endpoints added in recent weeks
> appear missing from `/docs`, and API calls return `404 Not Found` (Starlette route-miss) instead of
> the expected response. Running `python -m uvicorn` via the venv's own interpreter guarantees CWD is
> `sys.path[0]` and the live source is always loaded.

> **OneDrive + `--reload` note (Windows):** When the project lives inside an OneDrive-synced folder,
> uvicorn's `--reload` file watcher may not detect file changes. If you edit a route and the change
> doesn't appear in `/docs` after a few seconds, kill the server (`Ctrl+C`) and restart it manually
> rather than waiting for hot-reload to pick it up.

> **Android emulator note:** The emulator cannot reach `localhost` on your host machine. Use `http://10.0.2.2:8000/api/v1` in the Flutter app config. On a physical Android device, use your machine's LAN IP (e.g. `http://192.168.1.x:8000/api/v1`).

---

## Project Structure

```
app/
├── main.py                    # FastAPI app, lifespan (Firebase + Redis init)
├── config.py                  # Pydantic settings (reads from .env)
├── database.py                # Async SQLAlchemy engine + session
├── deps.py                    # FastAPI dependencies (get_db, get_current_user, require_role)
├── models/                    # SQLAlchemy ORM models
│   ├── user.py                # User (google_uid, email, role, is_verified)
│   ├── course.py              # Course (title, class_code, msg_window)
│   ├── enrollment.py          # Enrollment (user_id, course_id)
│   ├── unit.py                # Unit (label, status: draft/released/closed)
│   ├── disease.py             # Disease (key_symptoms, speech_style, nudge_behavior)
│   └── disease_document.py    # DiseaseDocument (upload record, version, parsed_at)
├── schemas/                   # Pydantic request/response schemas
├── routers/                   # FastAPI route handlers
│   ├── auth.py                # POST /auth/login, POST /auth/refresh
│   ├── users.py               # GET /users/me, PUT /users/me/role
│   ├── courses.py             # Course CRUD
│   ├── enrollments.py         # POST /enrollments/join, student list
│   └── disease_documents.py   # Upload + confirm disease docs
└── services/
    ├── auth_service.py        # Firebase token verify, JWT create/verify, refresh tokens
    ├── disease_parser.py      # JSON/CSV disease document parser
    └── file_storage.py        # Temp file storage for disease doc uploads
alembic/                       # Database migrations
scripts/
└── seed_test_users.py         # Creates dev test accounts in Firebase + PostgreSQL
tests/                         # pytest test suite
```

---

## API Overview

### Auth
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/auth/login` | None | Firebase ID token → access + refresh JWT |
| POST | `/api/v1/auth/refresh` | None | Rotate refresh token → new token pair |

### Users
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/users/me` | Bearer | Current user profile |
| PUT | `/api/v1/users/me/role` | Bearer | Set role (student/professor) — one-time only |

### Courses
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/courses` | Bearer | List courses (role-filtered) |
| POST | `/api/v1/courses` | Professor | Create course, auto-generate class code |
| GET | `/api/v1/courses/{id}` | Bearer | Course details |
| PUT | `/api/v1/courses/{id}` | Professor | Update course |
| DELETE | `/api/v1/courses/{id}/deactivate` | Professor | Soft deactivate |

### Enrollments
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/enrollments/join` | Student | Join course with 6-char class code |
| GET | `/api/v1/courses/{id}/students` | Professor | List enrolled students |
| DELETE | `/api/v1/courses/{id}/students/{uid}` | Professor | Remove student |

### Disease Documents
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/courses/{id}/disease-document` | Professor | Upload JSON/CSV — returns preview |
| POST | `/api/v1/courses/{id}/disease-document/confirm` | Professor | Commit parsed diseases to DB |

---

## Auth Flow

1. Flutter signs user in via Firebase (Google OAuth or email/password)
2. For email/password: Firebase enforces `email_verified = true` before the backend accepts the token
3. Flutter sends Firebase ID token to `POST /auth/login`
4. Backend verifies token with Firebase Admin SDK
5. Validates email is `@rutgers.edu` or `@scarletmail.rutgers.edu` (or `@test.pocketpatient.dev` when `allow_test_accounts=true`)
6. Creates or fetches user in PostgreSQL
7. Returns RS256 access token (15 min TTL) + refresh token (7 day TTL, stored hashed in Redis)
8. Flutter stores both tokens in secure storage and includes `Authorization: Bearer <access_token>` on all subsequent requests

---

## Database Migrations

```powershell
# Apply all pending migrations
uv run alembic upgrade head

# Roll back one migration
uv run alembic downgrade -1

# Auto-generate a new migration after model changes
uv run alembic revision --autogenerate -m "describe what changed"
```

---

## Testing

Tests run against a separate `pocketpatient_test` database (must exist — create it once with `createdb pocketpatient_test` or via psql). Tables are created and dropped automatically by the test suite; do not run migrations against it.

```powershell
# Full suite
uv run pytest -v

# Single file
uv run pytest tests/test_<name>.py -v
```

---

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `database_url` | Yes | Async PostgreSQL connection string |
| `redis_url` | Yes | Redis connection string |
| `secret_key` | Yes | App secret (unused in JWT flow, keep non-empty) |
| `firebase_project_id` | Yes | Firebase project ID (`pocket-patient-v2`) |
| `firebase_credentials_path` | Local dev | Path to service account JSON (not needed on Cloud Run) |
| `allow_test_accounts` | No | Set `true` in local `.env` to allow `@test.pocketpatient.dev` accounts. **Never enable in production.** |
| `jwt_private_key` | Yes | RS256 private key (newlines as `\n`) |
| `jwt_public_key` | Yes | RS256 public key (newlines as `\n`) |

---

## Local Test Accounts

Two pre-seeded accounts exist for local development. Use these on the login screen with email/password sign-in — no email verification required.

| Role | Email | Password |
|------|-------|----------|
| Student | `student@test.pocketpatient.dev` | `TestPass123!` |
| Professor | `professor@test.pocketpatient.dev` | `TestPass123!` |

To create them on a fresh local database, run:

```powershell
.venv\Scripts\activate
uv run python scripts/seed_test_users.py
```

Requires `allow_test_accounts=true` in `.env` (already set in the default `.env`). This flag is `False` by default and must never be enabled in production.

---

## Secrets — What NOT to Commit

The following files are gitignored and must never be pushed:

- `.env` — contains JWT keys and DB credentials
- `serviceAccountKey.json` — Firebase service account (full admin access)
- `*.pem` — raw key files
- `*firebase-adminsdk*.json` — any Firebase admin SDK credential file
