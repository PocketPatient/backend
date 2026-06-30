from app.main import app


def _schema():
    return app.openapi()


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
