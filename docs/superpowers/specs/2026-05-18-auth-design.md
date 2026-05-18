# Auth System Design — Week 2

**Date:** 2026-05-18  
**Scope:** Firebase token verification, custom JWT issuance, refresh token rotation, auth middleware, role selection  
**Stack:** FastAPI + async SQLAlchemy + PostgreSQL + Redis  

---

## 1. Schema Changes

Two Alembic migrations, applied in order:

### Migration A — make `role` nullable
`users.role` changes from `NOT NULL` to nullable. Users start with `role = NULL` and set it at the role-selection screen after first login.

### Migration B — add `is_verified`
New column `users.is_verified` (boolean, nullable):

| State | Value |
|-------|-------|
| Role not yet selected | `NULL` |
| Student (auto-approved) | `True` |
| Professor (pending approval) | `False` |

**ORM + schema updates:** `User` model and `UserOut` Pydantic schema both updated to reflect nullable `role` and optional `is_verified`.

---

## 2. Configuration (`app/config.py`)

Three new settings added to `Settings`:

| Env var | Purpose |
|---------|---------|
| `FIREBASE_PROJECT_ID` | Firebase project; used to scope `verify_id_token` |
| `JWT_PRIVATE_KEY` | RSA PEM (RS256 signing) |
| `JWT_PUBLIC_KEY` | RSA PEM (RS256 verification) |

Firebase Admin SDK is initialized at app startup in `main.py` via `firebase_admin.initialize_app()` using a service account credential from env.

A Redis async client is created at startup and stored on `app.state`.

---

## 3. `app/services/auth_service.py`

Single file owning all auth operations:

### `verify_firebase_token(id_token: str) -> dict`
1. Calls `firebase_admin.auth.verify_id_token(id_token)`
2. Extracts `uid`, `email`, `name`, `sign_in_provider`
3. Validates email domain: must end with `@scarletmail.rutgers.edu` or `@rutgers.edu`
4. Raises `HTTPException(403)` if domain invalid
5. Returns the extracted user data dict

### `get_or_create_user(db: AsyncSession, firebase_data: dict) -> User`
- Looks up `User` by `google_uid`
- If not found, creates new user with `role=None`, `is_verified=None`
- Returns the `User` ORM object

### `create_access_token(user: User) -> str`
- Payload: `{"sub": str(user.id), "email": user.email, "role": user.role.value if user.role else None, "exp": now + 15min}`
- Signed RS256 with `JWT_PRIVATE_KEY`

### `create_refresh_token(user_id: UUID, redis) -> str`
- Generates a 32-byte random hex token
- Stores `hash(token) → str(user_id)` in Redis with 7-day TTL
- Returns the raw token

### `verify_and_rotate_refresh_token(token: str, redis, db: AsyncSession) -> tuple[str, str]`
- Hashes the incoming token, looks up in Redis
- Raises `HTTPException(401)` if missing or expired
- Deletes the old token from Redis
- Loads the user from DB
- Issues new access token + new refresh token
- Returns `(new_access_token, new_refresh_token)`

---

## 4. Endpoints

### `POST /api/v1/auth/login`
**Body:** `{"firebase_id_token": "..."}`  
**Flow:** verify Firebase token → get_or_create_user → create_access_token + create_refresh_token  
**Response:** `{"access_token": "...", "refresh_token": "...", "token_type": "bearer"}`  
**Errors:** 403 if non-Rutgers email

### `POST /api/v1/auth/refresh`
**Body:** `{"refresh_token": "..."}`  
**Flow:** verify + delete old refresh token → issue new access token + new refresh token (rotation)  
**Response:** `{"access_token": "...", "refresh_token": "...", "token_type": "bearer"}`  
**Errors:** 401 if token invalid or expired

### `GET /api/v1/users/me`
**Auth:** `Authorization: Bearer <access_token>`  
**Response:** `UserOut` (current user profile)  
**Errors:** 401 if token missing/invalid

### `PUT /api/v1/users/me/role`
**Auth:** `Authorization: Bearer <access_token>`  
**Body:** `{"role": "student" | "professor"}`  
**Flow:**
- 409 if role already set
- Sets `role` on user
- `student` → `is_verified = True`
- `professor` → `is_verified = False`
**Response:** updated `UserOut`  
**Errors:** 401 unauthenticated, 409 role already set, 422 invalid role value

---

## 5. `app/deps.py`

Two FastAPI dependency functions:

### `get_current_user(authorization: str = Header(...), db: AsyncSession = Depends(get_db)) -> User`
1. Strips `Bearer ` prefix from header
2. Decodes RS256 JWT using `JWT_PUBLIC_KEY`
3. Loads `User` from DB by `sub` (UUID)
4. Raises `HTTPException(401)` on missing header, decode failure, or missing user

### `require_role(role: str) -> Callable`
Returns a dependency that:
- Calls `get_current_user` internally
- Raises `HTTPException(403)` if `user.role != role`

---

## 6. File Layout

```
app/
  config.py              # + FIREBASE_PROJECT_ID, JWT_PRIVATE_KEY, JWT_PUBLIC_KEY
  deps.py                # get_current_user, require_role  (new)
  main.py                # + firebase_admin.initialize_app(), redis client init
  models/user.py         # + is_verified, nullable role
  routers/
    auth.py              # POST /auth/login, POST /auth/refresh
    users.py             # GET /users/me, PUT /users/me/role  (new)
  schemas/user.py        # + is_verified, nullable role
  services/
    auth_service.py      # all auth logic  (new)
alembic/versions/
  <rev>_make_role_nullable.py
  <rev>_add_is_verified.py
requirements.txt         # + firebase-admin, redis[asyncio]
```

---

## 7. New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `firebase-admin` | latest | Firebase ID token verification |
| `redis[asyncio]` | latest | Async Redis client for refresh tokens |

`python-jose[cryptography]` is already in `requirements.txt` and handles RS256 JWT operations.

---

## 8. Error Handling Summary

| Condition | HTTP Status |
|-----------|-------------|
| Non-Rutgers email | 403 |
| Firebase token invalid/expired | 401 |
| JWT missing or invalid | 401 |
| Refresh token expired or not found | 401 |
| Role already set | 409 |
| User doesn't have required role | 403 |
