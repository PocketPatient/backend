from __future__ import annotations

import pytest

from app.services.disease_parser import ParsedDisease, ParsedUnit, ParseResult
from app.services.document_diff import DiffResult, ExistingDisease, compute_diff

_NUDGE = {"frequency": "rarely", "tone": "flat", "example": ""}


def _pd(name: str, difficulty_tier: int = 2, category: str = "Mood") -> ParsedDisease:
    return ParsedDisease(
        name=name,
        dsm_code=None,
        category=category,
        key_symptoms=["s1"],
        differentials=["d1"],
        difficulty_tier=difficulty_tier,
        speech_style="flat",
        nudge_behavior=_NUDGE,
    )


def _ed(name: str, unit_label: str, difficulty_tier: int = 2, category: str = "Mood") -> ExistingDisease:
    return ExistingDisease(
        name=name,
        unit_label=unit_label,
        dsm_code=None,
        category=category,
        key_symptoms=["s1"],
        differentials=["d1"],
        difficulty_tier=difficulty_tier,
        speech_style="flat",
        nudge_behavior=_NUDGE,
    )


def test_first_upload_all_added():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD"), _pd("GAD")])])
    result = compute_diff(pr, existing_units=[], existing_diseases=[])
    assert result.units_added == ["Unit 1"]
    assert result.units_orphaned == []
    assert result.diseases_added == 2
    assert result.diseases_modified == 0
    assert result.diseases_removed == 0


def test_new_disease_added_to_existing_unit():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD"), _pd("GAD")])])
    existing = [_ed("MDD", "Unit 1")]
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=existing)
    assert result.diseases_added == 1
    assert result.diseases_modified == 0
    assert result.diseases_removed == 0
    assert result.units_added == []


def test_disease_removed_from_existing_unit():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD")])])
    existing = [_ed("MDD", "Unit 1"), _ed("GAD", "Unit 1")]
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=existing)
    assert result.diseases_removed == 1
    assert result.diseases_added == 0
    assert result.diseases_modified == 0


def test_disease_modified():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD", difficulty_tier=3)])])
    existing = [_ed("MDD", "Unit 1", difficulty_tier=2)]
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=existing)
    assert result.diseases_modified == 1
    assert result.diseases_added == 0
    assert result.diseases_removed == 0


def test_unchanged_disease_not_counted():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD")])])
    existing = [_ed("MDD", "Unit 1")]
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=existing)
    assert result.diseases_modified == 0
    assert result.diseases_added == 0
    assert result.diseases_removed == 0


def test_new_unit_added():
    pr = ParseResult(units=[ParsedUnit(label="Unit 2", diseases=[_pd("GAD")])])
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=[])
    assert result.units_added == ["Unit 2"]
    assert result.diseases_added == 1


def test_unit_orphaned():
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD")])])
    existing_diseases = [_ed("GAD", "Unit 2"), _ed("Panic", "Unit 2")]
    result = compute_diff(
        pr,
        existing_units=["Unit 1", "Unit 2"],
        existing_diseases=existing_diseases,
    )
    assert "Unit 2" in result.units_orphaned
    assert result.diseases_removed == 2


def test_inactive_diseases_not_in_existing_list():
    # Caller is responsible for filtering is_active=False before calling compute_diff.
    # An empty existing_diseases means no soft-deleted diseases count as removed.
    pr = ParseResult(units=[ParsedUnit(label="Unit 1", diseases=[_pd("MDD")])])
    result = compute_diff(pr, existing_units=["Unit 1"], existing_diseases=[])
    assert result.diseases_removed == 0
