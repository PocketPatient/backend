from __future__ import annotations

import logging

import tiktoken

from app.models.message import Message, MessageRole

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 100_000
WARN_CONTEXT_TOKENS = 50_000
HEAD_KEEP = 5
OMITTED_NOTE = "[Earlier messages omitted for length]"

_encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding.encode(text))


def _to_content(message: Message) -> dict:
    role = "user" if message.role == MessageRole.student else "model"
    return {"role": role, "parts": [{"text": message.content}]}


def _message_tokens(message: Message) -> int:
    if message.token_count is not None:
        return message.token_count
    return count_tokens(message.content)


def build_history(messages: list[Message]) -> list[dict]:
    """Assemble the Gemini `contents` list from the transcript.

    Filters internal `system` notes, logs a warning past WARN_CONTEXT_TOKENS, and
    applies a first-HEAD_KEEP + trailing sliding window past MAX_CONTEXT_TOKENS.
    """
    visible = [m for m in messages if m.role != MessageRole.system]
    total = sum(_message_tokens(m) for m in visible)

    if total > WARN_CONTEXT_TOKENS:
        session_id = visible[0].session_id if visible else None
        logger.warning(
            "Session %s context is %d tokens (warn threshold %d)",
            session_id,
            total,
            WARN_CONTEXT_TOKENS,
        )

    if total <= MAX_CONTEXT_TOKENS:
        return [_to_content(m) for m in visible]

    head = visible[:HEAD_KEEP]
    tail_candidates = visible[HEAD_KEEP:]
    contents = [_to_content(m) for m in head]

    # Only window (and emit the omitted-note) when there are messages beyond the
    # head; if the head alone is over budget there is nothing to omit.
    if tail_candidates:
        # Always guarantee the most recent message is included.
        always_last = [tail_candidates[-1]]
        sliding_pool = tail_candidates[:-1]
        budget = MAX_CONTEXT_TOKENS - sum(_message_tokens(m) for m in head) - sum(
            _message_tokens(m) for m in always_last
        )
        tail: list[Message] = []
        for m in reversed(sliding_pool):
            t = _message_tokens(m)
            if t > budget:
                break
            tail.insert(0, m)
            budget -= t

        contents.append({"role": "user", "parts": [{"text": OMITTED_NOTE}]})
        contents.extend(_to_content(m) for m in tail)
        contents.extend(_to_content(m) for m in always_last)
    return contents
