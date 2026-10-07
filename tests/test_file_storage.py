import uuid
from pathlib import Path

import pytest

from app.services import file_storage


@pytest.fixture
def tmp_upload_root(tmp_path, monkeypatch):
    monkeypatch.setattr(file_storage, "UPLOAD_ROOT", tmp_path / "uploads")
    return tmp_path / "uploads"


def test_save_upload_creates_file(tmp_upload_root):
    course_id = uuid.uuid4()
    path_str = file_storage.save_upload(course_id, 1, "json", b'{"hello":"world"}')
    path = Path(path_str)
    assert path.exists()
    assert path.read_bytes() == b'{"hello":"world"}'
    assert str(course_id) in path_str
    assert path.name == "1.json"


def test_save_upload_creates_parent_dirs(tmp_upload_root):
    course_id = uuid.uuid4()
    file_storage.save_upload(course_id, 7, "csv", b"a,b,c")
    assert (tmp_upload_root / str(course_id) / "7.csv").exists()


def test_read_upload_returns_bytes(tmp_upload_root):
    course_id = uuid.uuid4()
    path = file_storage.save_upload(course_id, 1, "json", b"payload")
    assert file_storage.read_upload(path) == b"payload"


def test_upload_exists_true_after_save(tmp_upload_root):
    course_id = uuid.uuid4()
    path = file_storage.save_upload(course_id, 1, "json", b"x")
    assert file_storage.upload_exists(path) is True


def test_upload_exists_false_for_missing(tmp_upload_root):
    assert file_storage.upload_exists(str(tmp_upload_root / "nope.json")) is False


# ── GCS backend (production) ──────────────────────────────────────────────────

@pytest.fixture
def fake_gcs(monkeypatch):
    from unittest.mock import MagicMock

    from app.config import settings

    objects: dict[str, bytes] = {}

    def blob_for(bucket_name):
        def _blob(name):
            key = f"{bucket_name}/{name}"
            blob = MagicMock()
            blob.upload_from_string.side_effect = lambda raw: objects.__setitem__(key, raw)
            blob.download_as_bytes.side_effect = lambda: objects[key]
            blob.exists.side_effect = lambda: key in objects
            blob.delete.side_effect = lambda: objects.pop(key)
            return blob
        return _blob

    client = MagicMock()
    client.bucket.side_effect = lambda b: MagicMock(blob=blob_for(b))
    monkeypatch.setattr(file_storage, "_gcs_client", lambda: client)
    monkeypatch.setattr(settings, "gcs_upload_bucket", "pp-uploads")
    return objects


def test_gcs_round_trip(fake_gcs):
    course_id = uuid.uuid4()
    url = file_storage.save_upload(course_id, 3, "json", b"payload")
    assert url == f"gs://pp-uploads/disease-documents/{course_id}/3.json"
    assert file_storage.upload_exists(url) is True
    assert file_storage.read_upload(url) == b"payload"
    file_storage.delete_upload(url)
    assert file_storage.upload_exists(url) is False


def test_local_paths_still_readable_when_bucket_configured(fake_gcs, tmp_path):
    local = tmp_path / "old.json"
    local.write_bytes(b"legacy")
    assert file_storage.read_upload(str(local)) == b"legacy"
