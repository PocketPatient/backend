# PocketPatient API Guide

This guide covers the base URL, authentication flow, error format, and general conventions for the PocketPatient API. For the canonical endpoint reference see [`docs/api-contract.md`](api-contract.md). For live, interactive schema exploration visit `/docs` (Swagger UI) or `/redoc` (ReDoc) on a running server.

---

## 1. Base URL & Versioning

Every resource endpoint is mounted under the `/api/v1` prefix:

```
https://<host>/api/v1/<resource>
```

The health-check endpoint is unprefixed and does not require authentication:

```
GET /health
```

Response:

```json
{ "status": "ok", "timestamp": "2026-06-29T12:00:00.000000+00:00" }
```

Interactive documentation is available at:

- `/docs` — Swagger UI (try-it-out supported)
- `/redoc` — ReDoc (read-only, cleaner layout)

---

## 2. Auth Flow

### Step 1 — Google sign-in

The client performs a Google (Firebase) sign-in on the frontend and obtains a Firebase ID token.

### Step 2 — Exchange for a JWT

POST the Firebase ID token to the login endpoint:

```
POST /api/v1/auth/login
Content-Type: application/json

{
  "firebase_id_token": "<Firebase ID token>"
}
```

On success the server verifies the Firebase token, creates or retrieves the matching user record, and returns a short-lived RS256 JWT access token plus an opaque refresh token:

```json
{
  "access_token": "<RS256 JWT>",
  "refresh_token": "<opaque token>",
  "token_type": "bearer"
}
```

Possible error responses: `401 UNAUTHORIZED` (invalid Firebase token), `422 VALIDATION_ERROR` (malformed request body), `429 RATE_LIMIT_EXCEEDED`.

### Step 3 — Send the bearer token

Include the access token in the `Authorization` header on every subsequent request:

```
Authorization: Bearer <access_token>
```

### Token verification (`app/deps.py`)

`get_current_user` extracts the token from the `Authorization: Bearer` header, decodes it with RS256 using the server's public key (`settings.jwt_public_key`), reads the `sub` claim as the user UUID, and looks the user up in the database. Any of the following raises `401 UNAUTHORIZED`:

- Missing or malformed `Authorization` header
- JWT signature invalid, expired, or otherwise unverifiable
- `sub` claim not a valid UUID
- No user found for the given UUID

### Role enforcement (`require_role`)

Protected endpoints declare a role dependency:

```python
Depends(require_role("professor"))
# or
Depends(require_role("student"))
```

If the authenticated user's role does not match, the server returns `403 FORBIDDEN`.

### Step 4 — Refresh the access token

When the access token expires, obtain a new pair without re-authenticating via Google:

```
POST /api/v1/auth/refresh
Content-Type: application/json

{
  "refresh_token": "<opaque refresh token>"
}
```

The server verifies and rotates the refresh token (the old token is invalidated) and returns a fresh `TokenResponse` with the same shape as login. Possible error responses: `401 UNAUTHORIZED` (invalid or already-rotated refresh token), `422 VALIDATION_ERROR`, `429 RATE_LIMIT_EXCEEDED`.

---

## 3. Error Envelope

Every error response uses a consistent JSON envelope:

```json
{
  "detail": "<human-readable message or list>",
  "code": "<machine-readable code>"
}
```

The `code` field maps directly to the HTTP status:

| HTTP Status | `code`                |
|-------------|-----------------------|
| 400         | `BAD_REQUEST`         |
| 401         | `UNAUTHORIZED`        |
| 403         | `FORBIDDEN`           |
| 404         | `NOT_FOUND`           |
| 405         | `METHOD_NOT_ALLOWED`  |
| 409         | `CONFLICT`            |
| 410         | `GONE`                |
| 422         | `VALIDATION_ERROR`    |
| 429         | `RATE_LIMIT_EXCEEDED` |
| 500         | `INTERNAL_ERROR`      |

### 422 Validation errors

For `422 VALIDATION_ERROR`, `detail` is a **list** of Pydantic v2 error objects (not a single string). Each object follows the standard Pydantic error shape:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "firebase_id_token"],
      "msg": "Field required",
      "input": {},
      "url": "https://errors.pydantic.dev/..."
    }
  ],
  "code": "VALIDATION_ERROR"
}
```

---

## 4. Conventions

### Ownership and 404 vs 403

Endpoints that operate on a resource owned by another user return **404 NOT_FOUND**, not 403 FORBIDDEN. This avoids leaking the existence of resources to unauthorized callers.

### Pagination — `PaginatedSessions`

`GET /api/v1/sessions` returns a paginated response with this shape:

```json
{
  "items": [
    {
      "session_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "disease_name": "Major Depressive Disorder",
      "category": "Mood Disorders",
      "score": 82.0,
      "turn_count": 8,
      "started_at": "2026-09-15T09:00:00Z",
      "completed_at": "2026-09-15T09:45:00Z",
      "avg_response_latency_sec": 14.2
    }
  ],
  "total": 42,
  "page": 1,
  "page_size": 20
}
```

| Field       | Type            | Description                                              |
|-------------|-----------------|----------------------------------------------------------|
| `items`     | list            | Page of `CompletedSessionItem` objects (see below)       |
| `total`     | integer         | Total number of matching sessions across all pages       |
| `page`      | integer         | Current page number (1-based)                            |
| `page_size` | integer         | Number of items per page                                 |

Each `CompletedSessionItem` contains: `session_id`, `disease_name`, `category`, `score` (nullable float), `turn_count`, `started_at`, `completed_at` (nullable), `avg_response_latency_sec` (nullable float).

### Rate limiting

When a client exceeds the request rate limit the server responds with:

```
429 Too Many Requests

{ "detail": "...", "code": "RATE_LIMIT_EXCEEDED" }
```

Back off and retry after the delay indicated by any `Retry-After` header if present.

---

## 5. Further Reference

- **Endpoint reference** — [`docs/api-contract.md`](api-contract.md) lists every route, its required role, request/response schemas, and error codes.
- **Live schema** — `/docs` on a running server provides interactive Swagger UI with try-it-out. `/redoc` provides a read-only rendered version.
