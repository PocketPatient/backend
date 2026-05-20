from __future__ import annotations

import uuid
from pathlib import Path

UPLOAD_ROOT = Path("/tmp/pocketpatient-uploads")


def save_upload(course_id: uuid.UUID, version: int, ext: str, raw: bytes) -> str:
    path = UPLOAD_ROOT / str(course_id) / f"{version}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return str(path)


def read_upload(file_url: str) -> bytes:
    return Path(file_url).read_bytes()


def upload_exists(file_url: str) -> bool:
    return Path(file_url).exists()
