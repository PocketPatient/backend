import pytest

from app.services.sanitize import sanitize_text, strip_tags


def test_strip_tags_removes_html():
    assert strip_tags("<b>hello</b>") == "hello"


def test_strip_tags_removes_script_content_markup():
    # Tags are removed; inner text is kept (Flutter frontend never renders it as HTML).
    assert strip_tags("<script>alert(1)</script>hi") == "alert(1)hi"


def test_strip_tags_collapses_whitespace_and_trims():
    assert strip_tags("  a\n\n b\t c  ") == "a b c"


def test_strip_tags_plain_text_unchanged():
    assert strip_tags("Hello, how are you?") == "Hello, how are you?"


def test_sanitize_text_returns_cleaned_within_limit():
    assert sanitize_text("<i>ok</i>", 10) == "ok"


def test_sanitize_text_raises_when_cleaned_exceeds_limit():
    with pytest.raises(ValueError):
        sanitize_text("x" * 11, 10)


def test_sanitize_text_limit_applies_to_cleaned_not_raw():
    # Raw is 20 chars but cleaned "ok" is 2 — must pass under limit 5.
    assert sanitize_text("<span>ok</span>ok", 5) == "okok"
