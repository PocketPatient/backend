from __future__ import annotations

import json
from unittest.mock import patch


def test_logging_task_on_failure_logs_dead_letter():
    from app.tasks.base import LoggingTask

    task = LoggingTask()
    task.name = "app.tasks.demo"

    with patch("app.tasks.base.logger") as mock_log:
        task.on_failure(
            ValueError("boom"), "task-123", ("arg1",), {"k": "v"}, None
        )

    payload = json.loads(mock_log.error.call_args.args[0])
    assert payload["event"] == "dead-letter"
    assert payload["task"] == "app.tasks.demo"
    assert payload["task_id"] == "task-123"
    assert "boom" in payload["exc"]


def test_celery_uses_logging_task_base():
    from app.celery_app import celery
    from app.tasks.base import LoggingTask

    assert issubclass(celery.Task, LoggingTask)
