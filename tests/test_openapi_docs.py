import pytest

from app.main import app

_METHODS = {"get", "post", "put", "patch", "delete"}


def _schema():
    return app.openapi()


def _operations():
    schema = _schema()
    for path, item in schema["paths"].items():
        for method, op in item.items():
            if method in _METHODS:
                yield path, method, op


def test_app_metadata_present():
    schema = _schema()
    info = schema["info"]
    assert info["title"] == "PocketPatient API"
    assert info.get("description"), "app description must be set"
    # Every router domain is described in tags metadata.
    tag_names = {t["name"] for t in schema.get("tags", [])}
    expected = {
        "auth", "users", "courses", "units",
        "disease-documents", "enrollments", "sessions", "analytics",
    }
    assert expected <= tag_names, f"missing tag metadata: {expected - tag_names}"


def test_every_route_has_summary_and_tags():
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in _operations()
        if not op.get("summary") or not op.get("tags")
    ]
    assert not missing, f"routes missing summary/tags: {missing}"


def test_protected_routes_declare_401():
    # Auth endpoints under /auth/login and /auth/refresh are the only public posts.
    # /health is a public infrastructure endpoint that intentionally has no auth.
    public = {
        ("/api/v1/auth/login", "post"),
        ("/api/v1/auth/refresh", "post"),
        ("/health", "get"),
    }
    missing = [
        f"{method.upper()} {path}"
        for path, method, op in _operations()
        if (path, method) not in public and "401" not in op.get("responses", {})
    ]
    assert not missing, f"protected routes missing 401 response: {missing}"
