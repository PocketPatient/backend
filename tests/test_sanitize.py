import pytest
from pydantic import ValidationError

from app.services.sanitize import sanitize_text, strip_tags
from app.schemas.session import DiagnosisCreate, MessageCreate
from app.schemas.course import CourseCreate


def test_strip_tags_removes_html():
    assert strip_tags("<b>hello</b>") == "hello"


def test_strip_tags_removes_script_content_markup():
    # Tags are removed; inner text is kept (Flutter frontend never renders it as HTML).
    assert strip_tags("<script>alert(1)</script>hi") == "alert(1)hi"


def test_strip_tags_collapses_whitespace_and_trims():
    assert strip_tags("  a\n\n b\t c  ") == "a b c"


def test_strip_tags_plain_text_unchanged():
    assert strip_tags("Hello, how are you?") == "Hello, how are you?"


def test_strip_tags_preserves_unclosed_angle_bracket_clinical_text():
    assert strip_tags("mood <baseline and sleep poor") == "mood <baseline and sleep poor"


def test_strip_tags_preserves_bp_reading_with_angle_bracket():
    assert strip_tags("BP <120/80 today") == "BP <120/80 today"


def test_strip_tags_removes_real_tag_alongside_unclosed_bracket():
    assert strip_tags("<b>mood <baseline</b>") == "mood <baseline"


def test_sanitize_text_returns_cleaned_within_limit():
    assert sanitize_text("<i>ok</i>", 10) == "ok"


def test_sanitize_text_raises_when_cleaned_exceeds_limit():
    with pytest.raises(ValueError):
        sanitize_text("x" * 11, 10)


def test_sanitize_text_limit_applies_to_cleaned_not_raw():
    # Raw is 20 chars but cleaned "ok" is 2 — must pass under limit 5.
    assert sanitize_text("<span>ok</span>ok", 5) == "okok"


def test_message_create_strips_tags():
    m = MessageCreate(content="<b>Hi doctor</b>")
    assert m.content == "Hi doctor"


def test_message_create_rejects_over_2000_cleaned_chars():
    with pytest.raises(ValidationError):
        MessageCreate(content="x" * 2001)


def test_message_create_rejects_content_emptied_by_stripping():
    with pytest.raises(ValidationError):
        MessageCreate(content="<br><br>")


def test_diagnosis_create_strips_tags_in_fields():
    d = DiagnosisCreate(
        primary_dx="<i>MDD</i>",
        differentials=["<b>GAD</b>"],
        justification="Patient presents with <b>notable</b> " + "symptoms " * 5,
    )
    assert d.primary_dx == "MDD"
    assert d.differentials == ["GAD"]
    assert "<b>" not in d.justification and "</b>" not in d.justification


def test_diagnosis_create_rejects_primary_dx_emptied_by_stripping():
    with pytest.raises(ValidationError):
        DiagnosisCreate(primary_dx="<br>", justification="x" * 50)


def test_diagnosis_create_rejects_justification_under_min_after_stripping():
    # Raw length >= 50 but stripping the tags brings it below the 50-char minimum.
    raw = "<b>" + "x" * 47 + "</b>"
    assert len(raw) >= 50
    with pytest.raises(ValidationError):
        DiagnosisCreate(primary_dx="MDD", justification=raw)


def test_course_create_strips_tags_in_title():
    c = CourseCreate(title="<b>Intro to Psychiatry</b>")
    assert c.title == "Intro to Psychiatry"
