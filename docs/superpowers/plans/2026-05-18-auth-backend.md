# Week 2 Auth Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Firebase token verification, RS256 JWT issuance with refresh-token rotation via Redis, auth middleware, and role-selection endpoint for the PocketPatient FastAPI backend.

**Architecture:** Single `auth_service.py` owns all auth business logic (Firebase verification, JWT ops, Redis refresh-token management). FastAPI dependencies in `deps.py` inject the authenticated user into protected routes. The existing `auth.py` router gets login/refresh endpoints; a new `users.py` router handles `/me` and role selection.

**Tech Stack:** FastAPI 0.111, SQLAlchemy 2.0 (async), PostgreSQL 16, Redis (asyncio), firebase-admin, python-jose[cryptography], Alembic, pytest + pytest-asyncio

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Modify | `requirements.txt` | Add firebase-admin, redis, test deps |
| Modify | `app/models/user.py` | Nullable role, add is_verified, str enum |
| Modify | `app/schemas/user.py` | Nullable role, add is_verified |
| Create | `alembic/versions/<rev>_make_role_nullable.py` | DB migration |
| Create | `alembic/versions/<rev>_add_is_verified.py` | DB migration |
| Modify | `app/config.py` | Add FIREBASE_PROJECT_ID, JWT_PRIVATE_KEY, JWT_PUBLIC_KEY |
| Modify | `.env.example` | Document new env vars |
| Modify | `app/main.py` | Lifespan: Firebase + Redis init, include users router |
| Create | `app/services/auth_service.py` | All auth logic |
| Create | `app/deps.py` | get_current_user, require_role |
| Modify | `app/routers/auth.py` | POST /auth/login, POST /auth/refresh |
| Create | `app/routers/users.py` | GET /users/me, PUT /users/me/role |
| Create | `pytest.ini` | asyncio_mode = auto |
| Create | `tests/__init__.py` | Package marker |
| Create | `tests/conftest.py` | Shared RSA key fixture |
| Create | `tests/test_auth_service.py` | Unit tests for auth_service |
| Create | `tests/test_deps.py` | Unit tests for deps |
| Create | `tests/test_auth_router.py` | Endpoint tests for login/refresh |
| Create | `tests/test_users_router.py` | Endpoint tests for /me and role |
| Modify | `docs/api-contract.md` | Update endpoint table |

---

## Task 1: Add package dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Update requirements.txt**

Replace the full contents of `requirements.txt`:

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
sqlalchemy[asyncio]==2.0.49
asyncpg==0.31.0
alembic==1.18.4
pydantic-settings==2.2.1
python-jose[cryptography]==3.3.0
httpx==0.27.0
firebase-admin==6.5.0
redis[asyncio]==5.0.4
pytest==8.2.0
pytest-asyncio==0.23.7
pytest-mock==3.14.0
```

- [ ] **Step 2: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add firebase-admin, redis, and test dependencies"
```

---

## Task 2: Schema — nullable role, add is_verified, update ORM + Alembic

**Files:**
- Modify: `app/models/user.py`
- Modify: `app/schemas/user.py`
- Create: `alembic/versions/<rev>_make_role_nullable.py`
- Create: `alembic/versions/<rev>_add_is_verified.py`

- [ ] **Step 1: Update app/models/user.py**

Replace the full file:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Boolean, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserRole(str, PyEnum):
    student = "student"
    professor = "professor"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    google_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[UserRole | None] = mapped_column(Enum(UserRole), nullable=True)
    is_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

Note: `UserRole` now inherits from `str` so Pydantic v2 coerces ORM enum values to schema enum values cleanly.

- [ ] **Step 2: Update app/schemas/user.py**

Replace the full file:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class UserRole(str, Enum):
    student = "student"
    professor = "professor"
    admin = "admin"


class UserOut(BaseModel):
    id: uuid.UUID
    google_uid: str
    email: str
    role: UserRole | None
    is_verified: bool | None
    display_name: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 3: Start the database**

```bash
docker compose up -d db
```

Expected: Postgres container running.

- [ ] **Step 4: Generate migration A — nullable role**

```bash
alembic revision --autogenerate -m "make_role_nullable"
```

Expected: a new file at `alembic/versions/<rev>_make_role_nullable.py`. Open it and confirm the `upgrade()` body contains:

```python
op.alter_column('users', 'role',
    existing_type=sa.Enum('student', 'professor', 'admin', name='userrole'),
    nullable=True)
```

If autogenerate misses it (it sometimes does for nullable changes), edit the file manually to match the above.

- [ ] **Step 5: Apply migration A**

```bash
alembic upgrade head
```

Expected: `Running upgrade aab0c0019f65 -> <rev>, make_role_nullable`

- [ ] **Step 6: Generate migration B — add is_verified**

```bash
alembic revision --autogenerate -m "add_is_verified"
```

Expected: a new file at `alembic/versions/<rev>_add_is_verified.py`. Confirm the `upgrade()` body contains:

```python
op.add_column('users', sa.Column('is_verified', sa.Boolean(), nullable=True))
```

- [ ] **Step 7: Apply migration B**

```bash
alembic upgrade head
```

Expected: `Running upgrade <prev_rev> -> <rev>, add_is_verified`

- [ ] **Step 8: Commit**

```bash
git add app/models/user.py app/schemas/user.py alembic/versions/
git commit -m "feat: make role nullable and add is_verified to users table"
```

---

## Task 3: Config — Firebase + JWT settings

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`

- [ ] **Step 1: Generate an RSA keypair for local dev**

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out /tmp/jwt_private.pem
openssl rsa -in /tmp/jwt_private.pem -pubout -out /tmp/jwt_public.pem
```

Convert each to a single-line value suitable for `.env`:

```bash
awk 'NF {printf "%s\\n",$0;}' /tmp/jwt_private.pem
awk 'NF {printf "%s\\n",$0;}' /tmp/jwt_public.pem
```

Copy the output of each — you will paste them into `.env` in Step 3.

- [ ] **Step 2: Update app/config.py**

Replace the full file:

```python
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pocketpatient"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "changeme"
    firebase_project_id: str = ""
    jwt_private_key: str = ""
    jwt_public_key: str = ""

    @field_validator("jwt_private_key", "jwt_public_key", mode="before")
    @classmethod
    def _expand_newlines(cls, v: str) -> str:
        return v.replace("\\n", "\n") if v else v


settings = Settings()
```

- [ ] **Step 3: Add to .env (do not commit this file)**

Open `.env` and append:

```bash
FIREBASE_PROJECT_ID=your-firebase-project-id
JWT_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n<paste single-line private key output>\n-----END PRIVATE KEY-----"
JWT_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\n<paste single-line public key output>\n-----END PUBLIC KEY-----"
```

Replace `your-firebase-project-id` with the actual Firebase project ID from Firebase Console.

- [ ] **Step 4: Update .env.example**

Append to `.env.example`:

```bash
# Firebase
FIREBASE_PROJECT_ID=your-firebase-project-id

# JWT (RS256) — generate with:
#   openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out jwt_private.pem
#   openssl rsa -in jwt_private.pem -pubout -out jwt_public.pem
#   Convert to single-line: awk 'NF {printf "%s\\n",$0;}' jwt_private.pem
JWT_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
JWT_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
```

- [ ] **Step 5: Commit**

```bash
git add app/config.py .env.example
git commit -m "feat: add Firebase and JWT RS256 config settings"
```

---

## Task 4: Update main.py — lifespan and users router

**Files:**
- Modify: `app/main.py`
- Create: `app/routers/users.py` (stub)

- [ ] **Step 1: Create the users router stub**

Create `app/routers/users.py`:

```python
from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])
```

- [ ] **Step 2: Replace app/main.py**

```python
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import firebase_admin
import redis.asyncio as aioredis
from fastapi import FastAPI

from app.config import settings
from app.routers import auth, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not firebase_admin._apps and settings.firebase_project_id:
        firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="PocketPatient API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
```

- [ ] **Step 3: Verify the app starts**

```bash
uvicorn app.main:app --reload
```

Expected: server starts at `http://localhost:8000`. Visit `http://localhost:8000/health` — confirm `{"status":"ok",...}`. Stop with Ctrl-C.

- [ ] **Step 4: Commit**

```bash
git add app/main.py app/routers/users.py
git commit -m "feat: add lifespan with Firebase and Redis init, include users router"
```

---

## Task 5: auth_service.py (TDD)

**Files:**
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_auth_service.py`
- Create: `app/services/auth_service.py`

- [ ] **Step 1: Create pytest.ini**

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 2: Create tests/\_\_init\_\_.py**

Create `tests/__init__.py` as an empty file.

- [ ] **Step 3: Create tests/conftest.py**

```python
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_keys():
    """Generate a test RSA keypair. Session-scoped so it runs once per test session."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem
```

- [ ] **Step 4: Write failing tests**

Create `tests/test_auth_service.py`:

```python
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from app.models.user import User, UserRole
from app.services.auth_service import (
    create_access_token,
    create_refresh_token,
    get_or_create_user,
    verify_and_rotate_refresh_token,
    verify_firebase_token,
)


# ── verify_firebase_token ──────────────────────────────────────────────────────

def test_verify_firebase_token_scarletmail():
    decoded = {
        "uid": "uid1",
        "email": "student@scarletmail.rutgers.edu",
        "name": "Alice",
        "firebase": {"sign_in_provider": "google.com"},
    }
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded):
        result = verify_firebase_token("valid-token")
    assert result["uid"] == "uid1"
    assert result["email"] == "student@scarletmail.rutgers.edu"
    assert result["sign_in_provider"] == "google.com"


def test_verify_firebase_token_rutgers_edu():
    decoded = {
        "uid": "uid2",
        "email": "prof@rutgers.edu",
        "name": "Bob",
        "firebase": {"sign_in_provider": "password"},
    }
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded):
        result = verify_firebase_token("valid-token")
    assert result["email"] == "prof@rutgers.edu"


def test_verify_firebase_token_non_rutgers_raises_403():
    decoded = {"uid": "uid3", "email": "test@gmail.com", "firebase": {"sign_in_provider": "google.com"}}
    with patch("app.services.auth_service.firebase_auth.verify_id_token", return_value=decoded):
        with pytest.raises(HTTPException) as exc:
            verify_firebase_token("valid-token")
    assert exc.value.status_code == 403


def test_verify_firebase_token_invalid_token_raises_401():
    with patch(
        "app.services.auth_service.firebase_auth.verify_id_token",
        side_effect=Exception("token expired"),
    ):
        with pytest.raises(HTTPException) as exc:
            verify_firebase_token("bad-token")
    assert exc.value.status_code == 401


# ── get_or_create_user ─────────────────────────────────────────────────────────

async def test_get_or_create_user_creates_new():
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)

    firebase_data = {"uid": "new-uid", "email": "new@rutgers.edu", "name": "New User"}
    user = await get_or_create_user(db, firebase_data)

    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    assert user.google_uid == "new-uid"
    assert user.email == "new@rutgers.edu"
    assert user.role is None
    assert user.is_verified is None


async def test_get_or_create_user_returns_existing():
    existing = User(google_uid="existing-uid", email="existing@rutgers.edu")
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=mock_result)

    firebase_data = {"uid": "existing-uid", "email": "existing@rutgers.edu", "name": "X"}
    user = await get_or_create_user(db, firebase_data)

    db.add.assert_not_called()
    assert user is existing


# ── create_access_token ────────────────────────────────────────────────────────

def test_create_access_token_payload(rsa_keys):
    private_pem, public_pem = rsa_keys
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = UserRole.student

    with patch("app.services.auth_service.settings") as mock_cfg:
        mock_cfg.jwt_private_key = private_pem
        token = create_access_token(user)

    payload = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert payload["sub"] == str(user.id)
    assert payload["email"] == "test@rutgers.edu"
    assert payload["role"] == "student"


def test_create_access_token_null_role(rsa_keys):
    private_pem, public_pem = rsa_keys
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = None

    with patch("app.services.auth_service.settings") as mock_cfg:
        mock_cfg.jwt_private_key = private_pem
        token = create_access_token(user)

    payload = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert payload["role"] is None


# ── create_refresh_token ───────────────────────────────────────────────────────

async def test_create_refresh_token_stores_in_redis():
    redis_mock = AsyncMock()
    user_id = uuid.uuid4()

    raw_token = await create_refresh_token(user_id, redis_mock)

    assert len(raw_token) == 64  # secrets.token_hex(32) = 64 hex chars
    expected_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    redis_mock.setex.assert_awaited_once_with(
        f"refresh:{expected_hash}",
        7 * 24 * 60 * 60,
        str(user_id),
    )


# ── verify_and_rotate_refresh_token ───────────────────────────────────────────

async def test_verify_and_rotate_success(rsa_keys):
    private_pem, public_pem = rsa_keys
    user_id = uuid.uuid4()
    raw_token = "a" * 64
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    existing_user = User()
    existing_user.id = user_id
    existing_user.email = "test@rutgers.edu"
    existing_user.role = UserRole.student

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=str(user_id))

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_user
    db.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.auth_service.settings") as mock_cfg:
        mock_cfg.jwt_private_key = private_pem
        access_token, new_refresh = await verify_and_rotate_refresh_token(raw_token, redis_mock, db)

    redis_mock.delete.assert_awaited_once_with(f"refresh:{token_hash}")
    payload = jwt.decode(access_token, public_pem, algorithms=["RS256"])
    assert payload["sub"] == str(user_id)
    assert len(new_refresh) == 64


async def test_verify_and_rotate_invalid_token_raises_401():
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await verify_and_rotate_refresh_token("bad-token", redis_mock, db)
    assert exc.value.status_code == 401
```

- [ ] **Step 5: Run — confirm they fail**

```bash
pytest tests/test_auth_service.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.auth_service'`

- [ ] **Step 6: Implement app/services/auth_service.py**

```python
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from firebase_admin import auth as firebase_auth
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User

_ACCESS_TOKEN_EXPIRE_MINUTES = 15
_REFRESH_TOKEN_EXPIRE_SECONDS = 7 * 24 * 60 * 60
_RUTGERS_DOMAINS = ("@scarletmail.rutgers.edu", "@rutgers.edu")


def verify_firebase_token(id_token: str) -> dict:
    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Firebase token")
    email: str = decoded.get("email", "")
    if not any(email.endswith(domain) for domain in _RUTGERS_DOMAINS):
        raise HTTPException(status_code=403, detail="Must use a Rutgers email address")
    return {
        "uid": decoded["uid"],
        "email": email,
        "name": decoded.get("name"),
        "sign_in_provider": decoded.get("firebase", {}).get("sign_in_provider"),
    }


async def get_or_create_user(db: AsyncSession, firebase_data: dict) -> User:
    result = await db.execute(select(User).where(User.google_uid == firebase_data["uid"]))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            google_uid=firebase_data["uid"],
            email=firebase_data["email"],
            display_name=firebase_data.get("name"),
            role=None,
            is_verified=None,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value if user.role else None,
        "exp": now + timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_private_key, algorithm="RS256")


async def create_refresh_token(user_id: uuid.UUID, redis) -> str:
    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await redis.setex(f"refresh:{token_hash}", _REFRESH_TOKEN_EXPIRE_SECONDS, str(user_id))
    return raw_token


async def verify_and_rotate_refresh_token(
    token: str, redis, db: AsyncSession
) -> tuple[str, str]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    key = f"refresh:{token_hash}"
    user_id_str = await redis.get(key)
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Refresh token invalid or expired")
    await redis.delete(key)
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id_str)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    access_token = create_access_token(user)
    new_refresh_token = await create_refresh_token(user.id, redis)
    return access_token, new_refresh_token
```

- [ ] **Step 7: Run — confirm they pass**

```bash
pytest tests/test_auth_service.py -v
```

Expected:
```
test_verify_firebase_token_scarletmail PASSED
test_verify_firebase_token_rutgers_edu PASSED
test_verify_firebase_token_non_rutgers_raises_403 PASSED
test_verify_firebase_token_invalid_token_raises_401 PASSED
test_get_or_create_user_creates_new PASSED
test_get_or_create_user_returns_existing PASSED
test_create_access_token_payload PASSED
test_create_access_token_null_role PASSED
test_create_refresh_token_stores_in_redis PASSED
test_verify_and_rotate_success PASSED
test_verify_and_rotate_invalid_token_raises_401 PASSED
11 passed
```

- [ ] **Step 8: Commit**

```bash
git add pytest.ini tests/__init__.py tests/conftest.py tests/test_auth_service.py app/services/auth_service.py
git commit -m "feat: implement auth_service — Firebase verify, JWT, Redis refresh tokens"
```

---

## Task 6: deps.py (TDD)

**Files:**
- Create: `tests/test_deps.py`
- Create: `app/deps.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_deps.py`:

```python
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt

from app.deps import get_current_user, require_role
from app.models.user import User, UserRole


def make_user(role=None):
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = role
    user.is_verified = None
    user.display_name = "Test"
    return user


async def test_get_current_user_valid_token(rsa_keys):
    private_pem, public_pem = rsa_keys
    user = make_user(UserRole.student)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "role": "student",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    token = jwt.encode(payload, private_pem, algorithm="RS256")

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user
    db.execute = AsyncMock(return_value=mock_result)

    with patch("app.deps.settings") as mock_cfg:
        mock_cfg.jwt_public_key = public_pem
        result = await get_current_user(authorization=f"Bearer {token}", db=db)

    assert result is user


async def test_get_current_user_missing_header_raises_401():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization=None, db=db)
    assert exc.value.status_code == 401


async def test_get_current_user_no_bearer_prefix_raises_401():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_current_user(authorization="token-without-bearer", db=db)
    assert exc.value.status_code == 401


async def test_get_current_user_invalid_token_raises_401():
    db = AsyncMock()
    with patch("app.deps.settings") as mock_cfg:
        mock_cfg.jwt_public_key = "not-a-real-key"
        with pytest.raises(HTTPException) as exc:
            await get_current_user(authorization="Bearer bad.token.here", db=db)
    assert exc.value.status_code == 401


async def test_require_role_matching_role_passes():
    user = make_user(UserRole.professor)
    dependency_fn = require_role("professor")
    result = await dependency_fn(current_user=user)
    assert result is user


async def test_require_role_wrong_role_raises_403():
    user = make_user(UserRole.student)
    dependency_fn = require_role("professor")
    with pytest.raises(HTTPException) as exc:
        await dependency_fn(current_user=user)
    assert exc.value.status_code == 403


async def test_require_role_no_role_raises_403():
    user = make_user(role=None)
    dependency_fn = require_role("student")
    with pytest.raises(HTTPException) as exc:
        await dependency_fn(current_user=user)
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run — confirm they fail**

```bash
pytest tests/test_deps.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.deps'`

- [ ] **Step 3: Implement app/deps.py**

```python
from __future__ import annotations

from typing import Callable

from fastapi import Depends, Header, HTTPException
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.jwt_public_key, algorithms=["RS256"])
        user_id: str = payload["sub"]
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_role(role: str) -> Callable:
    async def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role is None or current_user.role.value != role:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return current_user
    return dependency
```

- [ ] **Step 4: Run — confirm they pass**

```bash
pytest tests/test_deps.py -v
```

Expected:
```
test_get_current_user_valid_token PASSED
test_get_current_user_missing_header_raises_401 PASSED
test_get_current_user_no_bearer_prefix_raises_401 PASSED
test_get_current_user_invalid_token_raises_401 PASSED
test_require_role_matching_role_passes PASSED
test_require_role_wrong_role_raises_403 PASSED
test_require_role_no_role_raises_403 PASSED
7 passed
```

- [ ] **Step 5: Commit**

```bash
git add app/deps.py tests/test_deps.py
git commit -m "feat: add get_current_user and require_role FastAPI dependencies"
```

---

## Task 7: Auth router — POST /auth/login and POST /auth/refresh (TDD)

**Files:**
- Create: `tests/test_auth_router.py`
- Modify: `app/routers/auth.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth_router.py`:

```python
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.models.user import User, UserRole


def make_user():
    user = User()
    user.id = uuid.uuid4()
    user.email = "test@rutgers.edu"
    user.role = None
    user.is_verified = None
    user.display_name = "Test User"
    return user


@pytest.fixture
def client():
    app.state.redis = AsyncMock()
    return TestClient(app)


def test_login_returns_tokens(client):
    user = make_user()
    with patch("app.routers.auth.auth_service") as svc:
        svc.verify_firebase_token.return_value = {
            "uid": "uid1", "email": "test@rutgers.edu",
            "name": "Test", "sign_in_provider": "google.com",
        }
        svc.get_or_create_user = AsyncMock(return_value=user)
        svc.create_access_token.return_value = "acc-token"
        svc.create_refresh_token = AsyncMock(return_value="ref-token")

        response = client.post("/api/v1/auth/login", json={"firebase_id_token": "firebase-tok"})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "acc-token"
    assert body["refresh_token"] == "ref-token"
    assert body["token_type"] == "bearer"


def test_login_non_rutgers_returns_403(client):
    with patch("app.routers.auth.auth_service") as svc:
        svc.verify_firebase_token.side_effect = HTTPException(
            status_code=403, detail="Must use a Rutgers email address"
        )
        response = client.post("/api/v1/auth/login", json={"firebase_id_token": "bad-tok"})
    assert response.status_code == 403


def test_login_missing_body_returns_422(client):
    response = client.post("/api/v1/auth/login", json={})
    assert response.status_code == 422


def test_refresh_returns_new_tokens(client):
    with patch("app.routers.auth.auth_service") as svc:
        svc.verify_and_rotate_refresh_token = AsyncMock(
            return_value=("new-acc", "new-ref")
        )
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "old-ref"})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "new-acc"
    assert body["refresh_token"] == "new-ref"


def test_refresh_expired_token_returns_401(client):
    with patch("app.routers.auth.auth_service") as svc:
        svc.verify_and_rotate_refresh_token = AsyncMock(
            side_effect=HTTPException(status_code=401, detail="Refresh token invalid or expired")
        )
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": "expired"})
    assert response.status_code == 401
```

- [ ] **Step 2: Run — confirm they fail**

```bash
pytest tests/test_auth_router.py -v
```

Expected: tests for `/auth/login` and `/auth/refresh` fail with 404 (routes don't exist yet).

- [ ] **Step 3: Replace app/routers/auth.py**

```python
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    firebase_id_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    firebase_data = auth_service.verify_firebase_token(body.firebase_id_token)
    user = await auth_service.get_or_create_user(db, firebase_data)
    access_token = auth_service.create_access_token(user)
    refresh_token = await auth_service.create_refresh_token(user.id, request.app.state.redis)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    access_token, new_refresh = await auth_service.verify_and_rotate_refresh_token(
        body.refresh_token, request.app.state.redis, db
    )
    return TokenResponse(access_token=access_token, refresh_token=new_refresh)
```

- [ ] **Step 4: Run — confirm they pass**

```bash
pytest tests/test_auth_router.py -v
```

Expected:
```
test_login_returns_tokens PASSED
test_login_non_rutgers_returns_403 PASSED
test_login_missing_body_returns_422 PASSED
test_refresh_returns_new_tokens PASSED
test_refresh_expired_token_returns_401 PASSED
5 passed
```

- [ ] **Step 5: Commit**

```bash
git add app/routers/auth.py tests/test_auth_router.py
git commit -m "feat: implement POST /auth/login and POST /auth/refresh with token rotation"
```

---

## Task 8: Users router — GET /users/me and PUT /users/me/role (TDD)

**Files:**
- Create: `tests/test_users_router.py`
- Modify: `app/routers/users.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_users_router.py`:

```python
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import app
from app.models.user import User, UserRole


def make_user(role=None, is_verified=None):
    user = User()
    user.id = uuid.uuid4()
    user.google_uid = "test-uid"
    user.email = "test@rutgers.edu"
    user.role = role
    user.is_verified = is_verified
    user.display_name = "Test User"
    user.created_at = datetime.now(timezone.utc)
    return user


@pytest.fixture
def authed_client():
    """TestClient with get_current_user overridden to return a student user."""
    user = make_user(UserRole.student, is_verified=True)

    async def _override_user():
        return user

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user
    yield TestClient(app), user
    app.dependency_overrides.clear()


# ── GET /users/me ──────────────────────────────────────────────────────────────

def test_get_me_returns_user_profile(authed_client):
    client, user = authed_client
    response = client.get("/api/v1/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "test@rutgers.edu"
    assert body["role"] == "student"
    assert body["is_verified"] is True


def test_get_me_no_auth_returns_401():
    app.state.redis = AsyncMock()
    client = TestClient(app)
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401


# ── PUT /users/me/role ─────────────────────────────────────────────────────────

def test_set_role_student_succeeds():
    user = make_user(role=None, is_verified=None)

    async def _override_user():
        return user

    mock_db = AsyncMock()

    async def _override_db():
        yield mock_db

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db

    client = TestClient(app)
    response = client.put("/api/v1/users/me/role", json={"role": "student"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert user.role == UserRole.student
    assert user.is_verified is True


def test_set_role_professor_sets_is_verified_false():
    user = make_user(role=None, is_verified=None)

    async def _override_user():
        return user

    mock_db = AsyncMock()

    async def _override_db():
        yield mock_db

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db

    client = TestClient(app)
    response = client.put("/api/v1/users/me/role", json={"role": "professor"})

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert user.role == UserRole.professor
    assert user.is_verified is False


def test_set_role_already_set_returns_409():
    user = make_user(role=UserRole.student, is_verified=True)

    async def _override_user():
        return user

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user

    client = TestClient(app)
    response = client.put("/api/v1/users/me/role", json={"role": "professor"})

    app.dependency_overrides.clear()

    assert response.status_code == 409


def test_set_role_invalid_value_returns_422():
    user = make_user(role=None)

    async def _override_user():
        return user

    app.state.redis = AsyncMock()
    app.dependency_overrides[get_current_user] = _override_user

    client = TestClient(app)
    response = client.put("/api/v1/users/me/role", json={"role": "admin"})

    app.dependency_overrides.clear()

    assert response.status_code == 422
```

- [ ] **Step 2: Run — confirm they fail**

```bash
pytest tests/test_users_router.py -v
```

Expected: 404 errors since `GET /users/me` and `PUT /users/me/role` don't exist yet.

- [ ] **Step 3: Replace app/routers/users.py**

```python
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User, UserRole
from app.schemas.user import UserOut

router = APIRouter(prefix="/users", tags=["users"])


class RoleRequest(BaseModel):
    role: Literal["student", "professor"]


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me/role", response_model=UserOut)
async def set_role(
    body: RoleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role is not None:
        raise HTTPException(status_code=409, detail="Role already set")
    current_user.role = UserRole(body.role)
    current_user.is_verified = body.role == "student"
    db.add(current_user)
    await db.commit()
    await db.refresh(current_user)
    return current_user
```

- [ ] **Step 4: Run — confirm they pass**

```bash
pytest tests/test_users_router.py -v
```

Expected:
```
test_get_me_returns_user_profile PASSED
test_get_me_no_auth_returns_401 PASSED
test_set_role_student_succeeds PASSED
test_set_role_professor_sets_is_verified_false PASSED
test_set_role_already_set_returns_409 PASSED
test_set_role_invalid_value_returns_422 PASSED
6 passed
```

- [ ] **Step 5: Run the full suite**

```bash
pytest tests/ -v
```

Expected: all 29 tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/routers/users.py tests/test_users_router.py
git commit -m "feat: implement GET /users/me and PUT /users/me/role"
```

---

## Task 9: Update api-contract.md

**Files:**
- Modify: `docs/api-contract.md`

- [ ] **Step 1: Replace docs/api-contract.md**

```markdown
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
  "exp": <unix timestamp>
}
```

Algorithm: RS256. Access token TTL: 15 minutes. Refresh token TTL: 7 days (single-use, rotated on every refresh).
```

- [ ] **Step 2: Commit**

```bash
git add docs/api-contract.md
git commit -m "docs: update api-contract with Week 2 auth and users endpoints"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|-----------------|-----------|
| `verify_firebase_token` with domain check | Task 5 |
| `get_or_create_user` (role=None, is_verified=None) | Task 5 |
| `create_access_token` RS256, 15min | Task 5 |
| `create_refresh_token` Redis, 7-day TTL | Task 5 |
| `verify_and_rotate_refresh_token` (rotation) | Task 5 |
| `POST /auth/login` | Task 7 |
| `POST /auth/refresh` (returns both tokens) | Task 7 |
| `GET /users/me` | Task 8 |
| `PUT /users/me/role` (409 if already set) | Task 8 |
| student → is_verified=True | Task 8 |
| professor → is_verified=False | Task 8 |
| `get_current_user` dependency | Task 6 |
| `require_role` dependency | Task 6 |
| Alembic: nullable role | Task 2 |
| Alembic: add is_verified | Task 2 |
| FIREBASE_PROJECT_ID, JWT keys in config | Task 3 |
| Firebase + Redis lifespan init | Task 4 |

All spec requirements are covered. No placeholders. Types are consistent across tasks (e.g., `UserRole` in models and schemas both use the same string values; `auth_service` function signatures used in router tests match the implementations).
