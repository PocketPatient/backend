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


def _responses_for(path, method):
    for p, m, op in _operations():
        if p == path and m == method:
            return set(op.get("responses", {}).keys())
    raise AssertionError(f"operation not found: {method.upper()} {path}")


@pytest.mark.parametrize(
    "path, method, code",
    [
        ("/api/v1/sessions/{session_id}/messages", "post", "409"),
        ("/api/v1/sessions/{session_id}/diagnose", "post", "409"),
        ("/api/v1/sessions", "post", "409"),
        ("/api/v1/sessions", "get", "404"),
        ("/api/v1/enrollments/join", "post", "410"),
    ],
)
def test_endpoint_declares_raised_error_code(path, method, code):
    assert code in _responses_for(path, method), (
        f"{method.upper()} {path} must declare {code}"
    )


# Core response models that must carry a description and an example.
_CORE_SCHEMAS = [
    "CourseOut",
    "UserOut",
    "SessionOut",
    "TokenResponse",
    "StudentSummary",
    "ClassSummary",
    "NotificationPreferences",
]


@pytest.mark.parametrize("name", _CORE_SCHEMAS)
def test_core_schema_has_example(name):
    schemas = _schema()["components"]["schemas"]
    assert name in schemas, f"{name} not in OpenAPI components"
    assert schemas[name].get("example") or schemas[name].get("examples"), (
        f"{name} must declare an example"
    )
