from __future__ import annotations

import uuid

from app.models.message import Message, MessageRole
from app.services.context_window import (
    MAX_CONTEXT_TOKENS,
    OMITTED_NOTE,
    build_history,
    count_tokens,
)


def _msg(role: MessageRole, content: str, token_count: int | None = None) -> Message:
    return Message(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role=role,
        content=content,
        token_count=token_count,
    )


def test_count_tokens_is_positive_and_monotonic():
    assert count_tokens("hello") >= 1
    assert count_tokens("hello world foo bar") > count_tokens("hello")


def test_build_history_maps_roles_and_filters_system():
    messages = [
        _msg(MessageRole.patient, "Hi doctor", token_count=2),
        _msg(MessageRole.student, "How are you?", token_count=3),
        _msg(MessageRole.system, "[regenerated: ai_break]", token_count=None),
    ]
    history = build_history(messages)
    assert history == [
        {"role": "model", "parts": [{"text": "Hi doctor"}]},
        {"role": "user", "parts": [{"text": "How are you?"}]},
    ]


def test_build_history_windows_when_over_limit():
    big = "word " * (MAX_CONTEXT_TOKENS // 4)
    messages = [_msg(MessageRole.student, f"{i} {big}") for i in range(8)]
    history = build_history(messages)
    texts = [c["parts"][0]["text"] for c in history]
    assert texts[0].startswith("0 ")
    assert texts[4].startswith("4 ")
    assert OMITTED_NOTE in texts
    assert texts[-1].startswith("7 ")
