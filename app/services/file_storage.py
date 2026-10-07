from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path

from app.config import settings

# Local dev backend. Cloud Run's filesystem is per-instance memory, so production
# sets GCS_UPLOAD_BUCKET and uploads go to Cloud Storage instead (gs:// URLs).
UPLOAD_ROOT = Path("/tmp/pocketpatient-uploads")
_GCS_PREFIX = "gs://"
_GCS_OBJECT_PREFIX = "disease-documents"


@lru_cache(maxsize=1)
def _gcs_client():
    # Lazy import + cached client: authenticates via ADC (the Cloud Run
    # service account), so no key file is needed.
    from google.cloud import storage

    return storage.Client()


def _gcs_blob(file_url: str):
    bucket, _, name = file_url.removeprefix(_GCS_PREFIX).partition("/")
    return _gcs_client().bucket(bucket).blob(name)


def save_upload(course_id: uuid.UUID, version: int, ext: str, raw: bytes) -> str:
    relative = f"{course_id}/{version}.{ext}"
    if settings.gcs_upload_bucket:
        file_url = f"{_GCS_PREFIX}{settings.gcs_upload_bucket}/{_GCS_OBJECT_PREFIX}/{relative}"
        _gcs_blob(file_url).upload_from_string(raw)
        return file_url
    path = UPLOAD_ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return str(path)


def read_upload(file_url: str) -> bytes:
    if file_url.startswith(_GCS_PREFIX):
        return _gcs_blob(file_url).download_as_bytes()
    return Path(file_url).read_bytes()


def upload_exists(file_url: str) -> bool:
    if file_url.startswith(_GCS_PREFIX):
        return _gcs_blob(file_url).exists()
    return Path(file_url).exists()


def delete_upload(file_url: str) -> None:
    if file_url.startswith(_GCS_PREFIX):
        from google.api_core.exceptions import NotFound

        try:
            _gcs_blob(file_url).delete()
        except NotFound:
            pass
        return
    Path(file_url).unlink(missing_ok=True)
