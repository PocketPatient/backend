from __future__ import annotations

from celery import Celery
from celery.signals import task_prerun, worker_process_init

from app.config import settings

celery = Celery(
    "pocket_patient",
    broker=settings.redis_url,
    task_cls="app.tasks.base:LoggingTask",
    include=[
        "app.tasks.bot_reply",
        "app.tasks.nudge",
        "app.tasks.case_initiation",
        "app.tasks.push_notifications",
    ],
)
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


@task_prerun.connect
def _reset_db_engine_pool(**_kwargs: object) -> None:
    # Each task body runs inside its own asyncio.run() event loop. Pooled
    # asyncpg connections are bound to the loop that created them, so a
    # connection checked out from a previous task's (now-closed) loop raises
    # AttributeError: 'NoneType' object has no attribute 'send'. Drop the
    # pool before each task runs (close=False: the old connections'
    # transports are already dead, so don't try to close them) so this task
    # gets fresh connections bound to its own loop.
    from app.database import engine
    engine.sync_engine.dispose(close=False)
