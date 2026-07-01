from __future__ import annotations

import re
from html.parser import HTMLParser

_WHITESPACE_RE = re.compile(r"\s+")


class _TagStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def strip_tags(value: str) -> str:
    """Remove HTML tags, collapse runs of whitespace, and trim."""
    parser = _TagStripper()
    parser.feed(value)
    parser.close()
    text = parser.get_text()
    return _WHITESPACE_RE.sub(" ", text).strip()


def sanitize_text(value: str, max_len: int) -> str:
    """Strip tags/whitespace, then enforce a maximum length on the cleaned text."""
    cleaned = strip_tags(value)
    if len(cleaned) > max_len:
        raise ValueError(f"text exceeds maximum length of {max_len} characters")
    return cleaned
