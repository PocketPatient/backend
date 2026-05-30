from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.disease_parser import ParsedDisease, ParseResult


@dataclass
class ExistingDisease:
    name: str
    unit_label: str
    dsm_code: str | None
    category: str
    key_symptoms: list[str]
    differentials: list[str]
    difficulty_tier: int
    speech_style: str
    nudge_behavior: dict[str, Any]


@dataclass
class DiffResult:
    units_added: list[str]
    units_orphaned: list[str]
    diseases_added: int
    diseases_modified: int
    diseases_removed: int


def _fields_equal(parsed: ParsedDisease, existing: ExistingDisease) -> bool:
    return (
        parsed.dsm_code == existing.dsm_code
        and parsed.category == existing.category
        and parsed.key_symptoms == existing.key_symptoms
        and parsed.differentials == existing.differentials
        and parsed.difficulty_tier == existing.difficulty_tier
        and parsed.speech_style == existing.speech_style
        and parsed.nudge_behavior == existing.nudge_behavior
    )


def compute_diff(
    parse_result: ParseResult,
    existing_units: list[str],
    existing_diseases: list[ExistingDisease],
) -> DiffResult:
    """Compute the diff between a new ParseResult and the current active DB state.

    existing_units: all unit labels currently in the DB for this course (any status).
    existing_diseases: only is_active=True diseases for this course.
    """
    existing_lookup: dict[tuple[str, str], ExistingDisease] = {
        (d.unit_label, d.name): d for d in existing_diseases
    }
    existing_unit_set = set(existing_units)
    new_unit_labels = {u.label for u in parse_result.units}

    units_added = [u.label for u in parse_result.units if u.label not in existing_unit_set]
    units_orphaned = [label for label in existing_units if label not in new_unit_labels]

    diseases_added = 0
    diseases_modified = 0
    seen_keys: set[tuple[str, str]] = set()

    for unit in parse_result.units:
        for disease in unit.diseases:
            key = (unit.label, disease.name)
            seen_keys.add(key)
            if key not in existing_lookup:
                diseases_added += 1
            elif not _fields_equal(disease, existing_lookup[key]):
                diseases_modified += 1

    diseases_removed = sum(1 for key in existing_lookup if key not in seen_keys)

    return DiffResult(
        units_added=units_added,
        units_orphaned=units_orphaned,
        diseases_added=diseases_added,
        diseases_modified=diseases_modified,
        diseases_removed=diseases_removed,
    )
