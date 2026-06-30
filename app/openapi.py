"""Reusable OpenAPI documentation building blocks.

ERROR_RESPONSES documents the standard error envelope emitted by the exception
handlers in app/main.py: a JSON body of {"detail": ..., "code": <CODE>}.
"""
from __future__ import annotations


def _err(code: str, description: str, detail: str) -> dict:
    return {
        "description": description,
        "content": {
            "application/json": {"example": {"detail": detail, "code": code}}
        },
    }


ERROR_RESPONSES: dict[int, dict] = {
    400: _err("BAD_REQUEST", "Malformed request.", "Invalid request."),
    401: _err("UNAUTHORIZED", "Missing or invalid bearer token.", "Not authenticated."),
    403: _err("FORBIDDEN", "Authenticated but not allowed.", "Insufficient role."),
    404: _err("NOT_FOUND", "Resource not found or not owned by caller.", "Not found."),
    409: _err("CONFLICT", "Conflicts with existing state.", "Already exists."),
    422: _err("VALIDATION_ERROR", "Request body failed validation.", "Validation error."),
    429: _err("RATE_LIMIT_EXCEEDED", "Too many requests.", "Rate limit exceeded."),
}


def errors(*codes: int) -> dict[int, dict]:
    """Return the OpenAPI `responses` subset for the given status codes."""
    return {code: ERROR_RESPONSES[code] for code in codes}


TAGS_METADATA: list[dict] = [
    {"name": "auth", "description": "Login and token refresh (RS256 JWT)."},
    {"name": "users", "description": "Current-user profile, role, FCM token, notification prefs."},
    {"name": "courses", "description": "Course CRUD and class codes (professor)."},
    {"name": "units", "description": "Units within a course and their disease pool."},
    {"name": "disease-documents", "description": "Upload and confirm disease definition documents."},
    {"name": "enrollments", "description": "Joining courses and roster management."},
    {"name": "sessions", "description": "Student patient-simulation sessions, messaging, diagnosis."},
    {"name": "analytics", "description": "Student and professor analytics and CSV export."},
]
