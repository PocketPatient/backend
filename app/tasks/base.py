from __future__ import annotations

import json
import logging

from celery import Task

logger = logging.getLogger(__name__)


def _safe(value: object) -> str:
    try:
        return repr(value)
    except Exception:  # noqa: BLE001
        return "<unreprable>"


class LoggingTask(Task):
    """Default base task. on_failure fires only after retries are exhausted
    (Celery semantics), so this is the Redis-broker equivalent of a dead-letter
    queue: the finally-failed task is logged as a structured error for triage."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: ANN001
        logger.error(json.dumps({
            "event": "dead-letter",
            "task": self.name,
            "task_id": task_id,
            "args": _safe(args),
            "kwargs": _safe(kwargs),
            "exc": repr(exc),
        }))
        super().on_failure(exc, task_id, args, kwargs, einfo)
