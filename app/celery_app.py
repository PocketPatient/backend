from __future__ import annotations

from celery import Celery
from celery.signals import beat_init, task_prerun, worker_init, worker_process_init

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
        "app.tasks.account_deletion",
    ],
)
celery.conf.update(
    result_backend=settings.redis_url,
    # No code reads task results (no AsyncResult.get); skip storing them.
    task_ignore_result=True,
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
        "sweep-pending-firebase-deletions": {
            "task": "app.tasks.account_deletion.sweep_pending_firebase_deletions",
            "schedule": 3600.0,  # every hour
        },
    },
)


@worker_init.connect
@beat_init.connect
def _validate_config(**_kwargs: object) -> None:
    # Celery's signal dispatcher logs and swallows Exception subclasses raised by
    # receivers, so a plain raise would let the worker start half-configured.
    # SystemExit is not swallowed: the process exits non-zero.
    from app.config import ConfigError
    try:
        settings.validate_for_runtime("worker")
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None


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
