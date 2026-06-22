from __future__ import annotations

import json
from unittest.mock import patch

from sqlalchemy import create_engine, text


def test_slow_query_logged_above_threshold():
    from app.observability import register_slow_query_logging

    engine = create_engine("sqlite://")
    register_slow_query_logging(engine, threshold_ms=0.0)  # everything is "slow"

    with patch("app.observability.logger") as mock_log:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    events = [json.loads(c.args[0])["event"] for c in mock_log.warning.call_args_list]
    assert "slow_query" in events


def test_fast_query_not_logged_below_threshold():
    from app.observability import register_slow_query_logging

    engine = create_engine("sqlite://")
    register_slow_query_logging(engine, threshold_ms=10_000.0)  # nothing is "slow"

    with patch("app.observability.logger") as mock_log:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    assert mock_log.warning.call_count == 0
