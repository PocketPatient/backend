# Week 4 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement disease document upload + parsing for PocketPatient — Units/Diseases/DiseaseDocuments models, CSV+JSON parser, upload/preview/confirm endpoints with replace-on-reupload protected by a released-status check.

**Architecture:** New SQLAlchemy models (`units`, `diseases`, `disease_documents`) with cascade deletes. Pure-function parser in `app/services/disease_parser.py` returns `(units, errors)` for partial-success files. Thin file-storage wrapper at `app/services/file_storage.py` writes to `/tmp/pocketpatient-uploads/`. Router `app/routers/disease_documents.py` exposes upload + confirm endpoints. Confirm re-reads and re-parses the file (never trusts a cached parse) and replaces existing units atomically.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, asyncpg, Pydantic v2, Alembic, pytest-asyncio 0.23.7, httpx

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `app/models/unit.py` | `Unit` model + `unit_status` enum |
| Create | `app/models/disease.py` | `Disease` model with JSONB fields + difficulty CHECK |
| Create | `app/models/disease_document.py` | `DiseaseDocument` model (audit trail of uploads) |
| Modify | `app/models/__init__.py` | Re-export the three new models |
| Create | `alembic/versions/<rev>_add_units_diseases_disease_documents.py` | Migration for new tables and enum |
| Create | `app/services/file_storage.py` | `save_upload`, `read_upload`, `upload_exists` |
| Create | `app/services/disease_parser.py` | `parse_json`, `parse_csv`, `parse`, dataclasses |
| Create | `app/schemas/disease_document.py` | Preview/error/confirm Pydantic schemas |
| Create | `app/routers/disease_documents.py` | `POST /disease-document`, `POST /disease-document/confirm` |
| Modify | `app/main.py` | Register the new router |
| Modify | `tests/conftest.py` | Add new tables to the `_truncate_all` list |
| Create | `tests/fixtures/__init__.py` | (empty) make `fixtures` a package |
| Create | `tests/fixtures/sample_diseases.json` | 6 diseases across 2 units, used by tests + manual joint test |
| Create | `tests/test_file_storage.py` | Unit tests for save/read/exists |
| Create | `tests/test_disease_parser.py` | Pure-function unit tests for the parser |
| Create | `tests/test_disease_documents_router.py` | Integration tests for upload + confirm |
| Modify | `docs/api-contract.md` | Document new endpoints |

---

## Prerequisites

- The `pocketpatient_test` Postgres database already exists from week 3.
- All week-3 tests pass: `uv run pytest -v` is green.

---

## Task 1: Models + Alembic Migration

**Files:**
- Create: `app/models/unit.py`
- Create: `app/models/disease.py`
- Create: `app/models/disease_document.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/<rev>_add_units_diseases_disease_documents.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Create `app/models/unit.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UnitStatus(str, PyEnum):
    draft = "draft"
    released = "released"
    closed = "closed"


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[UnitStatus] = mapped_column(
        Enum(UnitStatus, name="unit_status"), nullable=False, server_default="draft"
    )
    release_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 2: Create `app/models/disease.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Disease(Base):
    __tablename__ = "diseases"
    __table_args__ = (
        CheckConstraint("difficulty_tier >= 1 AND difficulty_tier <= 5", name="ck_difficulty_tier_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dsm_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    key_symptoms: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    differentials: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    difficulty_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    speech_style: Mapped[str] = mapped_column(String(100), nullable=False)
    nudge_behavior: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 3: Create `app/models/disease_document.py`**

```python
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DiseaseDocument(Base):
    __tablename__ = "disease_documents"
    __table_args__ = (UniqueConstraint("course_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Update `app/models/__init__.py`**

Replace the file's contents with:

```python
from app.models.course import Course
from app.models.disease import Disease
from app.models.disease_document import DiseaseDocument
from app.models.enrollment import Enrollment
from app.models.unit import Unit, UnitStatus
from app.models.user import User

__all__ = [
    "User",
    "Course",
    "Enrollment",
    "Unit",
    "UnitStatus",
    "Disease",
    "DiseaseDocument",
]
```

- [ ] **Step 5: Update `tests/conftest.py` `_truncate_all` to include the new tables**

Find the `_truncate_all` function and replace its TRUNCATE statement:

```python
async def _truncate_all():
    """Open a fresh asyncpg connection (not pooled) to truncate tables."""
    conn = await asyncpg.connect(_ASYNCPG_DSN)
    try:
        await conn.execute(
            "TRUNCATE TABLE disease_documents, diseases, units, enrollments, courses, users CASCADE"
        )
    finally:
        await conn.close()
```

- [ ] **Step 6: Generate the Alembic migration**

```bash
cd /Users/mahirshah/PocketPatient/backend
uv run alembic revision --autogenerate -m "add units, diseases, disease_documents"
```

This creates a new file under `alembic/versions/`. Open it and verify it:
- Creates the `unit_status` enum type
- Creates `units`, `diseases`, `disease_documents` tables with the correct FKs and the `ondelete='CASCADE'` rules where the model specifies them
- Includes the `CheckConstraint` on `diseases.difficulty_tier`
- Includes the `UniqueConstraint` on `(disease_documents.course_id, disease_documents.version)`

If any cascade rules or constraints are missing, edit the migration file to add them. The `ondelete="CASCADE"` on the model maps to `ondelete='CASCADE'` in `sa.ForeignKeyConstraint`.

- [ ] **Step 7: Apply the migration to the dev database**

```bash
uv run alembic upgrade head
```

Expected: "Running upgrade <prev> -> <new>, add units, diseases, disease_documents" with no errors.

- [ ] **Step 8: Verify existing tests still pass**

The test DB uses `Base.metadata.create_all` at session start (not Alembic), so the new tables will be auto-created. Run the existing suite:

```bash
uv run pytest -v
```

Expected: all existing tests pass (no regressions).

- [ ] **Step 9: Commit**

```bash
git add app/models/unit.py app/models/disease.py app/models/disease_document.py app/models/__init__.py alembic/versions/ tests/conftest.py
git commit -m "feat: add Unit, Disease, DiseaseDocument models + migration"
```

---

## Task 2: File Storage Helper (TDD)

**Files:**
- Create: `tests/test_file_storage.py`
- Create: `app/services/file_storage.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_file_storage.py`:

```python
import uuid
from pathlib import Path

import pytest

from app.services import file_storage


@pytest.fixture
def tmp_upload_root(tmp_path, monkeypatch):
    monkeypatch.setattr(file_storage, "UPLOAD_ROOT", tmp_path / "uploads")
    return tmp_path / "uploads"


def test_save_upload_creates_file(tmp_upload_root):
    course_id = uuid.uuid4()
    path_str = file_storage.save_upload(course_id, 1, "json", b'{"hello":"world"}')
    path = Path(path_str)
    assert path.exists()
    assert path.read_bytes() == b'{"hello":"world"}'
    assert str(course_id) in path_str
    assert path.name == "1.json"


def test_save_upload_creates_parent_dirs(tmp_upload_root):
    course_id = uuid.uuid4()
    file_storage.save_upload(course_id, 7, "csv", b"a,b,c")
    assert (tmp_upload_root / str(course_id) / "7.csv").exists()


def test_read_upload_returns_bytes(tmp_upload_root):
    course_id = uuid.uuid4()
    path = file_storage.save_upload(course_id, 1, "json", b"payload")
    assert file_storage.read_upload(path) == b"payload"


def test_upload_exists_true_after_save(tmp_upload_root):
    course_id = uuid.uuid4()
    path = file_storage.save_upload(course_id, 1, "json", b"x")
    assert file_storage.upload_exists(path) is True


def test_upload_exists_false_for_missing(tmp_upload_root):
    assert file_storage.upload_exists(str(tmp_upload_root / "nope.json")) is False
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
uv run pytest tests/test_file_storage.py -v
```

Expected: FAILED — `ImportError: cannot import name 'file_storage'`.

- [ ] **Step 3: Create `app/services/file_storage.py`**

```python
from __future__ import annotations

import uuid
from pathlib import Path

UPLOAD_ROOT = Path("/tmp/pocketpatient-uploads")


def save_upload(course_id: uuid.UUID, version: int, ext: str, raw: bytes) -> str:
    path = UPLOAD_ROOT / str(course_id) / f"{version}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return str(path)


def read_upload(file_url: str) -> bytes:
    return Path(file_url).read_bytes()


def upload_exists(file_url: str) -> bool:
    return Path(file_url).exists()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
uv run pytest tests/test_file_storage.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/file_storage.py tests/test_file_storage.py
git commit -m "feat: add file_storage service for disease document uploads"
```

---

## Task 3: JSON Parser (TDD)

**Files:**
- Create: `tests/test_disease_parser.py`
- Create: `app/services/disease_parser.py`

- [ ] **Step 1: Write the failing tests for JSON happy path**

Create `tests/test_disease_parser.py`:

```python
import pytest

from app.services.disease_parser import (
    ParseError,
    ParseResult,
    ParsedDisease,
    ParsedUnit,
    parse,
    parse_json,
)

VALID_JSON = """
{
  "units": [
    {
      "label": "Unit 1: Mood Disorders",
      "diseases": [
        {
          "name": "Major Depressive Disorder",
          "dsm_code": "F32.1",
          "category": "Mood Disorders",
          "key_symptoms": ["depressed mood", "anhedonia"],
          "differentials": ["Bipolar II", "Adjustment Disorder"],
          "difficulty_tier": 2,
          "speech_style": "flat",
          "nudge_behavior": {"frequency": "low", "tone": "withdrawn", "example": "I guess you're busy too"}
        }
      ]
    }
  ]
}
"""


def test_parse_json_happy_path():
    result = parse_json(VALID_JSON)
    assert result.errors == []
    assert len(result.units) == 1
    unit = result.units[0]
    assert unit.label == "Unit 1: Mood Disorders"
    assert len(unit.diseases) == 1
    disease = unit.diseases[0]
    assert disease.name == "Major Depressive Disorder"
    assert disease.dsm_code == "F32.1"
    assert disease.difficulty_tier == 2
    assert disease.key_symptoms == ["depressed mood", "anhedonia"]
    assert disease.nudge_behavior == {
        "frequency": "low",
        "tone": "withdrawn",
        "example": "I guess you're busy too",
    }
```

- [ ] **Step 2: Run — expect ImportError**

```bash
uv run pytest tests/test_disease_parser.py -v
```

Expected: FAILED — module does not exist.

- [ ] **Step 3: Create minimal `app/services/disease_parser.py` to pass the happy path**

```python
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

    if not ok:
        return None

    dsm = raw.get("dsm_code")
    if dsm is not None and not isinstance(dsm, str):
        dsm = str(dsm)
    if isinstance(dsm, str) and not dsm.strip():
        dsm = None

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
```

- [ ] **Step 4: Run — expect PASS**

```bash
uv run pytest tests/test_disease_parser.py::test_parse_json_happy_path -v
```

Expected: 1 passed.

- [ ] **Step 5: Add tests for JSON error cases**

Append to `tests/test_disease_parser.py`:

```python
def test_parse_json_invalid_json():
    result = parse_json("{not json")
    assert result.units == []
    assert len(result.errors) == 1
    assert "invalid JSON" in result.errors[0].message


def test_parse_json_missing_required_name():
    text = """
    {"units":[{"label":"U","diseases":[
        {"dsm_code":"F1","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert any("name" in e.location and "missing" in e.message for e in result.errors)
    assert result.units[0].diseases == []


def test_parse_json_difficulty_out_of_range():
    text = """
    {"units":[{"label":"U","diseases":[
        {"name":"X","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":99,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert any("difficulty_tier" in e.location for e in result.errors)


def test_parse_json_partial_success():
    text = """
    {"units":[{"label":"U","diseases":[
        {"name":"Good","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}},
        {"name":"","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert len(result.units) == 1
    assert len(result.units[0].diseases) == 1
    assert result.units[0].diseases[0].name == "Good"
    assert len(result.errors) >= 1


def test_parse_json_empty_units_list_is_ok():
    result = parse_json('{"units": []}')
    assert result.units == []
    assert result.errors == []


def test_parse_json_dsm_code_optional():
    text = """
    {"units":[{"label":"U","diseases":[
        {"name":"X","category":"C","key_symptoms":["s"],"differentials":["d"],
         "difficulty_tier":1,"speech_style":"flat","nudge_behavior":{"frequency":"low","tone":"flat","example":""}}
    ]}]}
    """
    result = parse_json(text)
    assert result.errors == []
    assert result.units[0].diseases[0].dsm_code is None


def test_parse_dispatch_unknown_extension():
    with pytest.raises(ValueError, match="unsupported"):
        parse("file.txt", b"data")


def test_parse_dispatch_json():
    result = parse("doc.json", VALID_JSON.encode("utf-8"))
    assert result.errors == []
    assert len(result.units) == 1
```

- [ ] **Step 6: Run all JSON parser tests**

```bash
uv run pytest tests/test_disease_parser.py -v
```

Expected: 8 passed (the `parse("doc.csv", ...)` path is not yet tested, just `parse("doc.json", ...)`).

- [ ] **Step 7: Commit**

```bash
git add app/services/disease_parser.py tests/test_disease_parser.py
git commit -m "feat: add JSON disease document parser with validation"
```

---

## Task 4: CSV Parser (TDD)

**Files:**
- Modify: `tests/test_disease_parser.py`
- Modify: `app/services/disease_parser.py`

- [ ] **Step 1: Add failing CSV tests**

Append to `tests/test_disease_parser.py`:

```python
VALID_CSV = """unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example
Unit 1: Mood,Major Depressive Disorder,F32.1,Mood Disorders,depressed mood;anhedonia,Bipolar II;Adjustment Disorder,2,flat,low,withdrawn,I guess you're busy too
Unit 1: Mood,Bipolar I,F31.1,Mood Disorders,mania;elevated mood,Bipolar II;Schizoaffective,3,pressured,high,urgent,I have the best idea ever
Unit 2: Anxiety,Generalized Anxiety,F41.1,Anxiety Disorders,worry;tension,Panic;Adjustment,2,tangential,high,worried,Did you see my last message?
"""


def test_parse_csv_happy_path():
    result = parse_csv(VALID_CSV)
    assert result.errors == []
    assert len(result.units) == 2
    mood = next(u for u in result.units if u.label == "Unit 1: Mood")
    assert len(mood.diseases) == 2
    assert mood.diseases[0].key_symptoms == ["depressed mood", "anhedonia"]
    assert mood.diseases[0].nudge_behavior == {
        "frequency": "low",
        "tone": "withdrawn",
        "example": "I guess you're busy too",
    }
    anxiety = next(u for u in result.units if u.label == "Unit 2: Anxiety")
    assert len(anxiety.diseases) == 1


def test_parse_csv_missing_required_field():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        "Unit 1,,F1,Mood,a;b,c;d,1,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert any("name" in e.location for e in result.errors)
    assert result.units == [] or all(u.diseases == [] for u in result.units)


def test_parse_csv_difficulty_tier_not_int():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        "Unit 1,X,F1,Mood,a;b,c;d,not-a-number,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert any("difficulty_tier" in e.location for e in result.errors)


def test_parse_csv_dsm_code_blank_becomes_none():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        "Unit 1,X,,Mood,a;b,c;d,1,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert result.errors == []
    assert result.units[0].diseases[0].dsm_code is None


def test_parse_csv_missing_unit_label():
    text = (
        "unit_label,disease_name,dsm_code,category,key_symptoms,differentials,difficulty_tier,speech_style,nudge_frequency,nudge_tone,nudge_example\n"
        ",X,F1,Mood,a;b,c;d,1,flat,low,flat,ex\n"
    )
    result = parse_csv(text)
    assert any("unit_label" in e.location for e in result.errors)


def test_parse_dispatch_csv():
    result = parse("doc.csv", VALID_CSV.encode("utf-8"))
    assert result.errors == []
    assert len(result.units) == 2
```

- [ ] **Step 2: Run — expect NotImplementedError or failures**

```bash
uv run pytest tests/test_disease_parser.py -v
```

Expected: the new CSV tests fail (NotImplementedError raised).

- [ ] **Step 3: Replace `parse_csv` in `app/services/disease_parser.py`**

Replace the `parse_csv` stub with this implementation:

```python
_CSV_REQUIRED_COLUMNS = {
    "unit_label",
    "disease_name",
    "dsm_code",
    "category",
    "key_symptoms",
    "differentials",
    "difficulty_tier",
    "speech_style",
    "nudge_frequency",
    "nudge_tone",
    "nudge_example",
}


def _split_semi(s: str) -> list[str]:
    return [part.strip() for part in s.split(";") if part.strip()]


def parse_csv(text: str) -> ParseResult:
    result = ParseResult()
    try:
        reader = csv.DictReader(io.StringIO(text))
    except csv.Error as e:
        result.errors.append(ParseError(location="<root>", message=f"invalid CSV: {e}"))
        return result

    if reader.fieldnames is None:
        result.errors.append(ParseError(location="<root>", message="CSV is empty or has no header"))
        return result

    missing = _CSV_REQUIRED_COLUMNS - set(reader.fieldnames)
    if missing:
        result.errors.append(
            ParseError(location="<root>", message=f"CSV missing required columns: {sorted(missing)}")
        )
        return result

    # Group rows by unit_label preserving first-seen order.
    units_in_order: list[str] = []
    units_by_label: dict[str, list[ParsedDisease]] = {}

    for row_idx, row in enumerate(reader, start=2):  # start=2 → header is row 1
        location = f"row {row_idx}"
        unit_label = (row.get("unit_label") or "").strip()
        if not unit_label:
            result.errors.append(ParseError(location=f"{location}.unit_label", message="unit_label is required"))
            continue

        tier_raw = (row.get("difficulty_tier") or "").strip()
        try:
            tier = int(tier_raw)
        except (TypeError, ValueError):
            result.errors.append(
                ParseError(location=f"{location}.difficulty_tier", message="difficulty_tier must be an integer between 1 and 5")
            )
            tier = None  # type: ignore[assignment]

        disease_dict = {
            "name": (row.get("disease_name") or "").strip(),
            "dsm_code": (row.get("dsm_code") or "").strip() or None,
            "category": (row.get("category") or "").strip(),
            "key_symptoms": _split_semi(row.get("key_symptoms") or ""),
            "differentials": _split_semi(row.get("differentials") or ""),
            "difficulty_tier": tier,
            "speech_style": (row.get("speech_style") or "").strip(),
            "nudge_behavior": {
                "frequency": (row.get("nudge_frequency") or "").strip(),
                "tone": (row.get("nudge_tone") or "").strip(),
                "example": (row.get("nudge_example") or "").strip(),
            },
        }

        parsed = _validate_disease(disease_dict, location, result.errors)
        if parsed is None:
            continue
        if unit_label not in units_by_label:
            units_by_label[unit_label] = []
            units_in_order.append(unit_label)
        units_by_label[unit_label].append(parsed)

    for label in units_in_order:
        result.units.append(ParsedUnit(label=label, diseases=units_by_label[label]))
    return result
```

Note: the `_validate_disease` from Task 3 already handles the rest of the validation. CSV adds only: header validation, integer-coercion of `difficulty_tier`, and `_split_semi` for arrays.

- [ ] **Step 4: Run all parser tests**

```bash
uv run pytest tests/test_disease_parser.py -v
```

Expected: all parser tests pass (JSON + CSV).

- [ ] **Step 5: Commit**

```bash
git add app/services/disease_parser.py tests/test_disease_parser.py
git commit -m "feat: add CSV disease document parser"
```

---

## Task 5: Schemas + Sample Fixture

**Files:**
- Create: `app/schemas/disease_document.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/sample_diseases.json`

- [ ] **Step 1: Create `app/schemas/disease_document.py`**

```python
from __future__ import annotations

import uuid

from pydantic import BaseModel


class UnitPreview(BaseModel):
    label: str
    disease_count: int
    diseases: list[str]


class ParseErrorOut(BaseModel):
    location: str
    message: str


class DiseaseDocumentPreview(BaseModel):
    document_id: uuid.UUID
    version: int
    units: list[UnitPreview]
    errors: list[ParseErrorOut]


class DiseaseDocumentConfirmResult(BaseModel):
    document_id: uuid.UUID
    version: int
    units_created: int
    diseases_created: int
```

- [ ] **Step 2: Create the test-fixtures package**

```bash
mkdir -p /Users/mahirshah/PocketPatient/backend/tests/fixtures
touch /Users/mahirshah/PocketPatient/backend/tests/fixtures/__init__.py
```

- [ ] **Step 3: Create `tests/fixtures/sample_diseases.json`**

```json
{
  "units": [
    {
      "label": "Unit 1: Mood Disorders",
      "diseases": [
        {
          "name": "Major Depressive Disorder",
          "dsm_code": "F32.1",
          "category": "Mood Disorders",
          "key_symptoms": ["depressed mood", "anhedonia", "insomnia", "fatigue"],
          "differentials": ["Bipolar II", "Adjustment Disorder", "Dysthymia"],
          "difficulty_tier": 2,
          "speech_style": "flat",
          "nudge_behavior": {"frequency": "low", "tone": "withdrawn", "example": "I guess you're busy too"}
        },
        {
          "name": "Bipolar I Disorder",
          "dsm_code": "F31.1",
          "category": "Mood Disorders",
          "key_symptoms": ["mania", "elevated mood", "decreased need for sleep", "grandiosity"],
          "differentials": ["Bipolar II", "Schizoaffective Disorder", "Substance-Induced Mood Disorder"],
          "difficulty_tier": 3,
          "speech_style": "pressured",
          "nudge_behavior": {"frequency": "high", "tone": "urgent", "example": "I just had the BEST idea, you have to hear it"}
        },
        {
          "name": "Bipolar II Disorder",
          "dsm_code": "F31.81",
          "category": "Mood Disorders",
          "key_symptoms": ["hypomania", "depressive episodes", "mood instability"],
          "differentials": ["Bipolar I", "MDD with mixed features", "Cyclothymia"],
          "difficulty_tier": 4,
          "speech_style": "variable",
          "nudge_behavior": {"frequency": "medium", "tone": "uneven", "example": "Some days I feel on top of the world"}
        }
      ]
    },
    {
      "label": "Unit 2: Anxiety Disorders",
      "diseases": [
        {
          "name": "Generalized Anxiety Disorder",
          "dsm_code": "F41.1",
          "category": "Anxiety Disorders",
          "key_symptoms": ["excessive worry", "muscle tension", "irritability", "sleep disturbance"],
          "differentials": ["Panic Disorder", "OCD", "Adjustment Disorder"],
          "difficulty_tier": 2,
          "speech_style": "tangential",
          "nudge_behavior": {"frequency": "high", "tone": "worried", "example": "Did you see my last message? Just checking"}
        },
        {
          "name": "Panic Disorder",
          "dsm_code": "F41.0",
          "category": "Anxiety Disorders",
          "key_symptoms": ["panic attacks", "fear of recurrence", "avoidance"],
          "differentials": ["GAD", "Specific Phobia", "Cardiac causes"],
          "difficulty_tier": 3,
          "speech_style": "rapid",
          "nudge_behavior": {"frequency": "medium", "tone": "fearful", "example": "I think something is really wrong with me"}
        },
        {
          "name": "Social Anxiety Disorder",
          "dsm_code": "F40.10",
          "category": "Anxiety Disorders",
          "key_symptoms": ["fear of judgment", "avoidance of social situations", "physical symptoms in public"],
          "differentials": ["Avoidant Personality", "Agoraphobia", "GAD"],
          "difficulty_tier": 2,
          "speech_style": "hesitant",
          "nudge_behavior": {"frequency": "low", "tone": "apologetic", "example": "Sorry to bother you again"}
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Commit**

```bash
git add app/schemas/disease_document.py tests/fixtures/
git commit -m "feat: add disease document schemas and sample fixture"
```

---

## Task 6: Upload Endpoint (TDD)

**Files:**
- Create: `tests/test_disease_documents_router.py`
- Create: `app/routers/disease_documents.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write failing tests for upload happy path + auth**

Create `tests/test_disease_documents_router.py`:

```python
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("clean_tables")

SAMPLE_DOC_PATH = Path(__file__).parent / "fixtures" / "sample_diseases.json"


def _sample_bytes() -> bytes:
    return SAMPLE_DOC_PATH.read_bytes()


async def _create_course(client, prof_token, title="Psych 101"):
    resp = await client.post(
        "/api/v1/courses",
        json={"title": title},
        headers={"Authorization": f"Bearer {prof_token}"},
    )
    assert resp.status_code == 201
    return resp.json()


async def test_upload_disease_document_happy_path(client, professor):
    _, token = professor
    course = await _create_course(client, token)

    files = {"file": ("sample.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 1
    assert body["errors"] == []
    assert len(body["units"]) == 2
    labels = [u["label"] for u in body["units"]]
    assert "Unit 1: Mood Disorders" in labels
    mood = next(u for u in body["units"] if u["label"] == "Unit 1: Mood Disorders")
    assert mood["disease_count"] == 3
    assert "Major Depressive Disorder" in mood["diseases"]


async def test_upload_unsupported_extension_returns_400(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    files = {"file": ("data.txt", b"hello", "text/plain")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


async def test_upload_by_student_returns_403(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    course = await _create_course(client, prof_token)

    files = {"file": ("doc.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 403


async def test_upload_to_nonexistent_course_returns_404(client, professor):
    _, token = professor
    import uuid as _uuid
    fake = _uuid.uuid4()
    files = {"file": ("doc.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{fake}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_upload_by_non_owner_professor_returns_404(client, professor, rsa_keys):
    _, owner_token = professor
    course = await _create_course(client, owner_token)

    import uuid as _uuid
    from datetime import datetime, timezone
    from app.models.user import User, UserRole
    from tests.conftest import _make_token, _TestSession
    private_pem, _ = rsa_keys
    other = User(
        id=_uuid.uuid4(),
        google_uid=f"otherprof-{_uuid.uuid4().hex}",
        email=f"otherprof-{_uuid.uuid4().hex[:8]}@test.edu",
        role=UserRole.professor,
        is_verified=False,
        display_name="Other Prof",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    async with _TestSession() as s:
        s.add(other)
        await s.commit()
    other_token = _make_token(other.id, private_pem)

    files = {"file": ("doc.json", _sample_bytes(), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


async def test_upload_with_parse_errors_returns_200_with_errors(client, professor):
    _, token = professor
    course = await _create_course(client, token)

    bad = {
        "units": [
            {
                "label": "U1",
                "diseases": [
                    {
                        "name": "Good",
                        "category": "C",
                        "key_symptoms": ["s"],
                        "differentials": ["d"],
                        "difficulty_tier": 1,
                        "speech_style": "flat",
                        "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                    },
                    {
                        "name": "",
                        "category": "C",
                        "key_symptoms": ["s"],
                        "differentials": ["d"],
                        "difficulty_tier": 1,
                        "speech_style": "flat",
                        "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                    },
                ],
            }
        ]
    }
    files = {"file": ("bad.json", json.dumps(bad).encode("utf-8"), "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["errors"]) >= 1
    assert body["units"][0]["disease_count"] == 1


async def test_upload_second_time_increments_version(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    files = {"file": ("doc.json", _sample_bytes(), "application/json")}

    r1 = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.json()["version"] == 1

    r2 = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.json()["version"] == 2
```

- [ ] **Step 2: Run — expect failures (router not registered)**

```bash
uv run pytest tests/test_disease_documents_router.py -v
```

Expected: FAILED — 404 on every request (route doesn't exist).

- [ ] **Step 3: Create `app/routers/disease_documents.py` with the upload endpoint**

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_role
from app.models.course import Course
from app.models.disease_document import DiseaseDocument
from app.models.user import User
from app.schemas.disease_document import (
    DiseaseDocumentPreview,
    ParseErrorOut,
    UnitPreview,
)
from app.services import disease_parser, file_storage

router = APIRouter(
    prefix="/courses/{course_id}/disease-document",
    tags=["disease-documents"],
)

_SUPPORTED_EXTENSIONS = {"json", "csv"}


async def _get_owned_course(
    course_id: uuid.UUID, current_user: User, db: AsyncSession
) -> Course:
    result = await db.execute(
        select(Course).where(Course.id == course_id, Course.professor_id == current_user.id)
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


def _extract_extension(filename: str | None) -> str:
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail=f"unsupported file extension: {ext!r} (expected .json or .csv)"
        )
    return ext


@router.post("", response_model=DiseaseDocumentPreview)
async def upload_disease_document(
    course_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = await _get_owned_course(course_id, current_user, db)
    ext = _extract_extension(file.filename)
    raw = await file.read()

    max_version = (
        await db.execute(
            select(func.coalesce(func.max(DiseaseDocument.version), 0)).where(
                DiseaseDocument.course_id == course.id
            )
        )
    ).scalar_one()
    next_version = max_version + 1

    file_url = file_storage.save_upload(course.id, next_version, ext, raw)

    doc = DiseaseDocument(
        course_id=course.id,
        uploaded_by=current_user.id,
        file_url=file_url,
        version=next_version,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    result = disease_parser.parse(file.filename, raw)
    return DiseaseDocumentPreview(
        document_id=doc.id,
        version=doc.version,
        units=[
            UnitPreview(
                label=u.label,
                disease_count=len(u.diseases),
                diseases=[d.name for d in u.diseases],
            )
            for u in result.units
        ],
        errors=[ParseErrorOut(location=e.location, message=e.message) for e in result.errors],
    )
```

- [ ] **Step 4: Register the router in `app/main.py`**

Edit `app/main.py` to add `disease_documents` to the imports and `include_router` calls:

```python
from app.routers import auth, courses, disease_documents, enrollments, users
```

```python
app.include_router(disease_documents.router, prefix="/api/v1")
```

(Insert the `include_router` line after the existing `courses.router` include.)

- [ ] **Step 5: Run all upload tests**

```bash
uv run pytest tests/test_disease_documents_router.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add app/routers/disease_documents.py app/main.py tests/test_disease_documents_router.py
git commit -m "feat: add disease document upload endpoint with parse preview"
```

---

## Task 7: Confirm Endpoint (TDD)

**Files:**
- Modify: `tests/test_disease_documents_router.py`
- Modify: `app/routers/disease_documents.py`

- [ ] **Step 1: Add failing tests for confirm**

Append to `tests/test_disease_documents_router.py`:

```python
async def _upload_sample(client, course_id, token, payload: bytes | None = None):
    data = payload if payload is not None else _sample_bytes()
    files = {"file": ("doc.json", data, "application/json")}
    resp = await client.post(
        f"/api/v1/courses/{course_id}/disease-document",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_confirm_happy_path(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_sample(client, course["id"], token)

    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["units_created"] == 2
    assert body["diseases_created"] == 6
    assert body["version"] == 1


async def test_confirm_with_no_pending_upload_returns_404(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_confirm_with_parse_errors_returns_400(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    bad = {
        "units": [
            {
                "label": "U1",
                "diseases": [
                    {
                        "name": "",
                        "category": "C",
                        "key_symptoms": ["s"],
                        "differentials": ["d"],
                        "difficulty_tier": 1,
                        "speech_style": "flat",
                        "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                    }
                ],
            }
        ]
    }
    await _upload_sample(client, course["id"], token, payload=json.dumps(bad).encode("utf-8"))

    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "errors" in body["detail"] or "parse" in str(body["detail"]).lower()


async def test_confirm_when_file_missing_returns_410(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    preview = await _upload_sample(client, course["id"], token)

    # Manually delete the uploaded file from disk to simulate expiry.
    from sqlalchemy import select
    from app.models.disease_document import DiseaseDocument
    from tests.conftest import _TestSession
    import uuid as _uuid
    from pathlib import Path

    doc_id = _uuid.UUID(preview["document_id"])
    async with _TestSession() as s:
        row = (await s.execute(select(DiseaseDocument).where(DiseaseDocument.id == doc_id))).scalar_one()
        Path(row.file_url).unlink()

    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 410


async def test_confirm_replaces_existing_units(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_sample(client, course["id"], token)
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Upload a smaller payload and confirm again
    smaller = {
        "units": [
            {
                "label": "Just one",
                "diseases": [
                    {
                        "name": "Solo",
                        "category": "Cat",
                        "key_symptoms": ["a"],
                        "differentials": ["b"],
                        "difficulty_tier": 1,
                        "speech_style": "flat",
                        "nudge_behavior": {"frequency": "low", "tone": "flat", "example": ""},
                    }
                ],
            }
        ]
    }
    await _upload_sample(client, course["id"], token, payload=json.dumps(smaller).encode("utf-8"))
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["units_created"] == 1
    assert body["diseases_created"] == 1

    # Verify only the new unit/disease remain
    import uuid as _uuid
    from sqlalchemy import select, func
    from app.models.unit import Unit
    from app.models.disease import Disease
    from tests.conftest import _TestSession
    async with _TestSession() as s:
        unit_count = (await s.execute(select(func.count()).select_from(Unit).where(Unit.course_id == _uuid.UUID(course["id"])))).scalar_one()
        disease_count = (await s.execute(select(func.count()).select_from(Disease))).scalar_one()
    assert unit_count == 1
    assert disease_count == 1


async def test_confirm_blocked_by_released_unit_returns_409(client, professor):
    _, token = professor
    course = await _create_course(client, token)
    await _upload_sample(client, course["id"], token)
    await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Mark one unit as released directly in DB
    import uuid as _uuid
    from sqlalchemy import select
    from app.models.unit import Unit, UnitStatus
    from tests.conftest import _TestSession
    async with _TestSession() as s:
        unit = (await s.execute(select(Unit).where(Unit.course_id == _uuid.UUID(course["id"])))).scalars().first()
        unit.status = UnitStatus.released
        await s.commit()

    await _upload_sample(client, course["id"], token)
    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409


async def test_confirm_by_student_returns_403(client, professor, student):
    _, prof_token = professor
    _, stu_token = student
    course = await _create_course(client, prof_token)
    await _upload_sample(client, course["id"], prof_token)

    resp = await client.post(
        f"/api/v1/courses/{course['id']}/disease-document/confirm",
        headers={"Authorization": f"Bearer {stu_token}"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run — expect 404 on confirm route**

```bash
uv run pytest tests/test_disease_documents_router.py -k confirm -v
```

Expected: FAILED — confirm route doesn't exist yet.

- [ ] **Step 3: Add `confirm_disease_document` to `app/routers/disease_documents.py`**

Append to the router:

```python
from datetime import datetime, timezone

from app.models.disease import Disease
from app.models.unit import Unit, UnitStatus
from app.schemas.disease_document import DiseaseDocumentConfirmResult


@router.post("/confirm", response_model=DiseaseDocumentConfirmResult)
async def confirm_disease_document(
    course_id: uuid.UUID,
    current_user: User = Depends(require_role("professor")),
    db: AsyncSession = Depends(get_db),
):
    course = await _get_owned_course(course_id, current_user, db)

    doc_result = await db.execute(
        select(DiseaseDocument)
        .where(DiseaseDocument.course_id == course.id, DiseaseDocument.parsed_at.is_(None))
        .order_by(DiseaseDocument.uploaded_at.desc())
        .limit(1)
    )
    doc = doc_result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="No pending upload to confirm")

    if not file_storage.upload_exists(doc.file_url):
        raise HTTPException(status_code=410, detail="Upload file expired, please re-upload")

    raw = file_storage.read_upload(doc.file_url)
    filename = f"doc.{doc.file_url.rsplit('.', 1)[-1]}"
    parse_result = disease_parser.parse(filename, raw)

    if parse_result.errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "File has parse errors; cannot confirm",
                "errors": [
                    {"location": e.location, "message": e.message} for e in parse_result.errors
                ],
            },
        )

    existing_units = (
        await db.execute(select(Unit).where(Unit.course_id == course.id))
    ).scalars().all()
    if any(u.status == UnitStatus.released for u in existing_units):
        raise HTTPException(
            status_code=409,
            detail="Close all released units before re-uploading",
        )

    for u in existing_units:
        await db.delete(u)
    await db.flush()

    units_created = 0
    diseases_created = 0
    for parsed_unit in parse_result.units:
        unit = Unit(course_id=course.id, label=parsed_unit.label)
        db.add(unit)
        await db.flush()
        units_created += 1
        for d in parsed_unit.diseases:
            db.add(
                Disease(
                    unit_id=unit.id,
                    name=d.name,
                    dsm_code=d.dsm_code,
                    category=d.category,
                    key_symptoms=d.key_symptoms,
                    differentials=d.differentials,
                    difficulty_tier=d.difficulty_tier,
                    speech_style=d.speech_style,
                    nudge_behavior=d.nudge_behavior,
                )
            )
            diseases_created += 1

    doc.parsed_at = datetime.now(timezone.utc)
    await db.commit()

    return DiseaseDocumentConfirmResult(
        document_id=doc.id,
        version=doc.version,
        units_created=units_created,
        diseases_created=diseases_created,
    )
```

- [ ] **Step 4: Run all confirm tests**

```bash
uv run pytest tests/test_disease_documents_router.py -k confirm -v
```

Expected: 7 passed.

- [ ] **Step 5: Run the full test suite — confirm no regressions**

```bash
uv run pytest -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add app/routers/disease_documents.py tests/test_disease_documents_router.py
git commit -m "feat: add disease document confirm endpoint with replace-on-reupload"
```

---

## Task 8: Update API Contract

**Files:**
- Modify: `docs/api-contract.md`

- [ ] **Step 1: Insert a Disease Documents section in `docs/api-contract.md` between Enrollments and Health**

Add this section after the Enrollments section, before `## Health`:

```markdown
## Disease Documents

| Method | Path | Description | Auth | Status |
|--------|------|-------------|------|--------|
| POST | `/api/v1/courses/{course_id}/disease-document` | Upload a disease document (CSV or JSON) and return preview | Bearer JWT | ✅ Week 4 |
| POST | `/api/v1/courses/{course_id}/disease-document/confirm` | Commit the most recent pending upload — replaces existing units | Bearer JWT | ✅ Week 4 |

### POST /api/v1/courses/{course_id}/disease-document
**Role required:** professor (must own the course)
**Request:** `multipart/form-data` with a single `file` field (`.csv` or `.json`)
**Behavior:** Stores the raw file, creates a `disease_documents` row with the next per-course version, parses and returns a preview. Does **not** create Unit/Disease rows.
**Response (200):**
```json
{
  "document_id": "uuid",
  "version": 1,
  "units": [
    {"label": "Unit 1: Mood Disorders", "disease_count": 3, "diseases": ["MDD", "Bipolar I", "Bipolar II"]}
  ],
  "errors": [
    {"location": "row 5", "message": "missing required field: difficulty_tier"}
  ]
}
```
**Errors:** 400 unsupported extension, 401 unauthenticated, 403 not a professor, 404 course not found or not owner

### POST /api/v1/courses/{course_id}/disease-document/confirm
**Role required:** professor (must own the course)
**Behavior:** Finds the latest unparsed upload for this course, re-reads and re-parses the file, then (if no parse errors and no released units) deletes existing units and inserts the new ones atomically. Sets `parsed_at` on the document row.
**Response (200):**
```json
{"document_id": "uuid", "version": 1, "units_created": 2, "diseases_created": 6}
```
**Errors:**
- 400 — parse errors present (`detail.errors` lists them); nothing committed
- 401 — unauthenticated
- 403 — not a professor
- 404 — course not found, or no pending upload to confirm
- 409 — at least one existing unit has `status = 'released'`
- 410 — upload file no longer exists on disk (re-upload required)

---
```

- [ ] **Step 2: Commit**

```bash
git add docs/api-contract.md
git commit -m "docs: document week 4 disease-document endpoints in API contract"
```

---

## Final Verification

- [ ] **Run the full test suite end-to-end**

```bash
cd /Users/mahirshah/PocketPatient/backend
uv run pytest -v
```

Expected: all tests pass (week 2 + week 3 + week 4 suites).

- [ ] **Manual smoke test (optional)**

Start the server:

```bash
uv run uvicorn app.main:app --reload
```

Then with `curl`:

```bash
# Create a course (use the auth token from a real login or generate a test JWT)
TOKEN="<your professor JWT>"
COURSE_ID=$(curl -s -X POST http://localhost:8000/api/v1/courses \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Smoke Test"}' | jq -r .id)

# Upload the sample fixture
curl -s -X POST "http://localhost:8000/api/v1/courses/$COURSE_ID/disease-document" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@tests/fixtures/sample_diseases.json"

# Confirm
curl -s -X POST "http://localhost:8000/api/v1/courses/$COURSE_ID/disease-document/confirm" \
  -H "Authorization: Bearer $TOKEN"
```

Expected: preview shows 2 units / 6 diseases, confirm returns `{"units_created": 2, "diseases_created": 6}`.

---

## Self-Review

**Spec coverage check:**

| Spec Requirement | Task |
|------------------|------|
| `units` table with `unit_status` enum | Task 1 |
| `diseases` table with JSONB fields + difficulty CHECK | Task 1 |
| `disease_documents` table with `UNIQUE(course_id, version)` | Task 1 |
| Cascade delete units → diseases, courses → units/disease_documents | Task 1 |
| Alembic migration | Task 1 |
| Pure parser (CSV + JSON) returning `(units, errors)` | Tasks 3, 4 |
| All required-field validation rules | Tasks 3, 4 |
| File storage at `/tmp/pocketpatient-uploads/{course_id}/{version}.{ext}` | Task 2 |
| `POST /disease-document` (upload + preview) | Task 6 |
| Version auto-increments per course | Task 6 |
| Preview includes `errors[]` for partial-success files | Task 6 |
| `POST /disease-document/confirm` | Task 7 |
| Re-parse file on confirm (don't trust cache) | Task 7 |
| 410 when file missing | Task 7 |
| 409 when any unit is `released` | Task 7 |
| Replace-all semantics on re-upload | Task 7 |
| `parsed_at` set on confirm | Task 7 |
| Sample fixture with 6 diseases / 2 units | Task 5 |
| API contract documented | Task 8 |
| Auth: 403 for student, 404 for non-owner professor | Tasks 6, 7 |

All spec requirements covered. ✅
