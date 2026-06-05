from __future__ import annotations

from celery import Celery
from celery.signals import worker_process_init

from app.config import settings

celery = Celery("pocket_patient", broker=settings.redis_url)
celery.conf.update(
    result_backend=settings.redis_url,
    timezone="UTC",
    beat_schedule={
        "check-for-new-cases": {
            "task": "app.tasks.case_initiation.check_and_initiate_cases",
            "schedule": 900.0,  # every 15 minutes
        },
        "check-for-nudges": {
            "task": "app.tasks.nudge.check_and_send_nudges",
            "schedule": 3600.0,  # every hour
        },
    },
)


@worker_process_init.connect
def _init_firebase(**_kwargs: object) -> None:
    from app.services.firebase import init_firebase
    init_firebase()
