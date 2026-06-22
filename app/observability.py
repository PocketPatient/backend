from __future__ import annotations

import json
import logging
import time

from sqlalchemy import event
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_START_KEY = "_pp_query_start"


def register_slow_query_logging(engine: Engine, threshold_ms: float = 50.0) -> None:
    """Log any SQL statement whose execution exceeds threshold_ms. `engine` is the
    sync Engine (for an async engine, pass engine.sync_engine)."""

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info[_START_KEY] = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        start = conn.info.pop(_START_KEY, None)
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms > threshold_ms:
            logger.warning(json.dumps({
                "event": "slow_query",
                "duration_ms": round(elapsed_ms, 2),
                "statement": " ".join(statement.split())[:300],
            }))
