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
