from __future__ import annotations

import asyncio
import threading
from typing import Any
from collections.abc import Coroutine


def run_task_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run `coro` on a dedicated thread with its own event loop.

    Celery task bodies call this instead of asyncio.run(). Running on a fresh
    thread keeps each task's event loop isolated (so the per-task pool reset in
    app.celery_app still applies) and, crucially, works even when a loop is
    already running in the calling thread — which is the case under Celery's
    task_always_eager mode inside pytest-asyncio tests.
    """
    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's thread
            box["exc"] = exc

    thread = threading.Thread(target=_runner)
    thread.start()
    thread.join()
    if "exc" in box:
        raise box["exc"]
    return box["result"]
