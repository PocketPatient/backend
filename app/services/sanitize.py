from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"</?[a-zA-Z][^<>]*>")


def strip_tags(value: str) -> str:
    """Remove complete HTML tags, collapse runs of whitespace, and trim.

    Only well-formed tags (``<tag ...>`` / ``</tag>``) are removed. A bare
    ``<`` that isn't part of a complete tag (e.g. clinical shorthand like
    "mood <baseline" or "BP <120/80") is left intact rather than truncating
    the rest of the text. The tag body excludes both ``<`` and ``>`` (not
    just ``>``) so a stray ``<`` can't be swallowed into an unrelated later
    tag's match (e.g. "<b>mood <baseline</b>" must not let "<baseline</b>"
    be treated as one tag).
    """
    text = TAG_RE.sub("", value)
    return _WHITESPACE_RE.sub(" ", text).strip()


def sanitize_text(value: str, max_len: int) -> str:
    """Strip tags/whitespace, then enforce a maximum length on the cleaned text."""
    cleaned = strip_tags(value)
    if len(cleaned) > max_len:
        raise ValueError(f"text exceeds maximum length of {max_len} characters")
    return cleaned
