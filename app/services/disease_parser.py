from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any


@dataclass
class ParsedDisease:
    name: str
    dsm_code: str | None
    category: str
    key_symptoms: list[str]
    differentials: list[str]
    difficulty_tier: int
    speech_style: str
    nudge_behavior: dict[str, Any]


@dataclass
class ParsedUnit:
    label: str
    diseases: list[ParsedDisease]


@dataclass
class ParseError:
    location: str
    message: str


@dataclass
class ParseResult:
    units: list[ParsedUnit] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)


_REQUIRED_STR_FIELDS = ("name", "category", "speech_style")


def _validate_disease(raw: dict[str, Any], path: str, errors: list[ParseError]) -> ParsedDisease | None:
    ok = True
    for f in _REQUIRED_STR_FIELDS:
        v = raw.get(f)
        if not isinstance(v, str) or not v.strip():
            errors.append(ParseError(location=f"{path}.{f}", message=f"missing or empty required field: {f}"))
            ok = False

    tier = raw.get("difficulty_tier")
    if not isinstance(tier, int) or isinstance(tier, bool) or tier < 1 or tier > 5:
        errors.append(ParseError(location=f"{path}.difficulty_tier", message="difficulty_tier must be an integer between 1 and 5"))
        ok = False

    key_symptoms = raw.get("key_symptoms")
    if not isinstance(key_symptoms, list) or not key_symptoms or not all(isinstance(s, str) and s.strip() for s in key_symptoms):
        errors.append(ParseError(location=f"{path}.key_symptoms", message="key_symptoms must be a non-empty list of strings"))
        ok = False

    differentials = raw.get("differentials")
    if not isinstance(differentials, list) or not differentials or not all(isinstance(s, str) and s.strip() for s in differentials):
        errors.append(ParseError(location=f"{path}.differentials", message="differentials must be a non-empty list of strings"))
        ok = False

    nudge = raw.get("nudge_behavior")
    if not isinstance(nudge, dict):
        errors.append(ParseError(location=f"{path}.nudge_behavior", message="nudge_behavior must be an object"))
        ok = False
    else:
        for f in ("frequency", "tone"):
            v = nudge.get(f)
            if not isinstance(v, str) or not v.strip():
                errors.append(ParseError(location=f"{path}.nudge_behavior.{f}", message=f"missing or empty required field: {f}"))
                ok = False

    dsm_raw = raw.get("dsm_code")
    dsm: str | None
    if dsm_raw is None:
        dsm = None
    elif isinstance(dsm_raw, str):
        dsm = dsm_raw.strip() or None
    else:
        errors.append(ParseError(location=f"{path}.dsm_code", message="dsm_code must be a string"))
        dsm = None
        ok = False

    if not ok:
        return None

    return ParsedDisease(
        name=raw["name"].strip(),
        dsm_code=dsm,
        category=raw["category"].strip(),
        key_symptoms=[s.strip() for s in key_symptoms],
        differentials=[s.strip() for s in differentials],
        difficulty_tier=tier,
        speech_style=raw["speech_style"].strip(),
        nudge_behavior={
            "frequency": nudge["frequency"].strip(),
            "tone": nudge["tone"].strip(),
            "example": (nudge.get("example") or "").strip() if isinstance(nudge.get("example"), str) else "",
        },
    )


def parse_json(text: str) -> ParseResult:
    result = ParseResult()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        result.errors.append(ParseError(location="<root>", message=f"invalid JSON: {e}"))
        return result

    if not isinstance(data, dict):
        result.errors.append(ParseError(location="<root>", message="JSON root must be an object"))
        return result

    units_raw = data.get("units")
    if not isinstance(units_raw, list):
        result.errors.append(ParseError(location="units", message="units must be a list"))
        return result

    for ui, unit_raw in enumerate(units_raw):
        if not isinstance(unit_raw, dict):
            result.errors.append(ParseError(location=f"units[{ui}]", message="unit must be an object"))
            continue
        label = unit_raw.get("label")
        if not isinstance(label, str) or not label.strip():
            result.errors.append(ParseError(location=f"units[{ui}].label", message="unit label is required"))
            continue
        diseases_raw = unit_raw.get("diseases")
        if not isinstance(diseases_raw, list):
            result.errors.append(ParseError(location=f"units[{ui}].diseases", message="diseases must be a list"))
            continue
        parsed_diseases: list[ParsedDisease] = []
        for di, disease_raw in enumerate(diseases_raw):
            if not isinstance(disease_raw, dict):
                result.errors.append(ParseError(location=f"units[{ui}].diseases[{di}]", message="disease must be an object"))
                continue
            parsed = _validate_disease(disease_raw, f"units[{ui}].diseases[{di}]", result.errors)
            if parsed is not None:
                parsed_diseases.append(parsed)
        result.units.append(ParsedUnit(label=label.strip(), diseases=parsed_diseases))
    return result


def parse_csv(text: str) -> ParseResult:
    raise NotImplementedError


def parse(filename: str, raw: bytes) -> ParseResult:
    ext = PurePath(filename).suffix.lower().lstrip(".")
    text = raw.decode("utf-8")
    if ext == "json":
        return parse_json(text)
    if ext == "csv":
        return parse_csv(text)
    raise ValueError(f"unsupported file extension: {ext!r}")
