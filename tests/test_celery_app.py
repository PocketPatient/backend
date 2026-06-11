from __future__ import annotations

from unittest.mock import patch

from celery.signals import task_prerun


def test_task_prerun_resets_db_engine_pool():
    """Each Celery task body runs inside its own asyncio.run() event loop.

    Pooled asyncpg connections are bound to the loop that created them, so a
    connection checked out from a previous task's (now-closed) loop raises
    AttributeError: 'NoneType' object has no attribute 'send'. The
    task_prerun handler must drop the pool (without closing existing
    connections, since their transports are already dead) before each task
    runs, so it gets fresh connections bound to its own loop.
    """
    import app.celery_app  # noqa: F401  (registers the task_prerun receiver)

    with patch("app.database.engine.sync_engine.dispose") as mock_dispose:
        task_prerun.send(sender=None)

    mock_dispose.assert_called_once_with(close=False)
