from __future__ import annotations

import asyncio

import pytest


def test_run_task_async_returns_result():
    from app.tasks._run import run_task_async

    async def coro():
        return 42

    assert run_task_async(coro()) == 42


def test_run_task_async_propagates_exception():
    from app.tasks._run import run_task_async

    async def coro():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        run_task_async(coro())


async def test_run_task_async_works_inside_running_loop():
    # asyncio_mode=auto means a loop is already running here; bare asyncio.run
    # would raise. run_task_async must still work.
    from app.tasks._run import run_task_async

    async def coro():
        return "ok"

    assert run_task_async(coro()) == "ok"
