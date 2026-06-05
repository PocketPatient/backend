from __future__ import annotations

import firebase_admin
import firebase_admin.credentials

from app.config import settings


def init_firebase() -> None:
    if firebase_admin._apps:
        return
    if settings.firebase_credentials_path:
        cred = firebase_admin.credentials.Certificate(settings.firebase_credentials_path)
        firebase_admin.initialize_app(cred)
    elif settings.firebase_project_id:
        firebase_admin.initialize_app(options={"projectId": settings.firebase_project_id})
