# Week 4 Backend Design — Disease Document Upload + Parsing

**Date:** 2026-05-20
**Scope:** Backend tasks from `week-04.md` — Units/Diseases/DiseaseDocuments models, parser, upload/preview/confirm endpoints

---

## 1. Goal

Professors upload a CSV or JSON disease document describing the units and diseases for a course. The system parses the file, returns a preview for confirmation, and on confirm writes Unit and Disease records to the database.

---

## 2. Data Models

Three new tables. Field types follow `week-04.md` exactly.

### `units`
| Column | Type | Constraints |
|--------|------|------------|
| `id` | UUID | PK |
| `course_id` | UUID | FK → `courses.id`, `ON DELETE CASCADE` |
| `label` | VARCHAR(100) | NOT NULL |
| `status` | `unit_status` ENUM | NOT NULL, default `'draft'` |
| `release_date` | TIMESTAMP w/ tz | nullable |
| `created_at` | TIMESTAMP w/ tz | server default `now()` |

New Postgres ENUM `unit_status` with values `draft`, `released`, `closed`.

### `diseases`
| Column | Type | Constraints |
|--------|------|------------|
| `id` | UUID | PK |
| `unit_id` | UUID | FK → `units.id`, `ON DELETE CASCADE` |
| `name` | VARCHAR(255) | NOT NULL |
| `dsm_code` | VARCHAR(20) | nullable |
| `category` | VARCHAR(100) | NOT NULL |
| `key_symptoms` | JSONB | NOT NULL, array of strings |
| `differentials` | JSONB | NOT NULL, array of strings |
| `difficulty_tier` | INT | NOT NULL, 1–5 (CHECK constraint) |
| `speech_style` | VARCHAR(100) | NOT NULL |
| `nudge_behavior` | JSONB | NOT NULL, `{frequency, tone, example}` |
| `is_active` | BOOLEAN | server default `true` |
| `created_at` | TIMESTAMP w/ tz | server default `now()` |

### `disease_documents`
| Column | Type | Constraints |
|--------|------|------------|
| `id` | UUID | PK |
| `course_id` | UUID | FK → `courses.id`, `ON DELETE CASCADE` |
| `uploaded_by` | UUID | FK → `users.id` |
| `file_url` | TEXT | NOT NULL |
| `version` | INT | NOT NULL, unique per `course_id` |
| `uploaded_at` | TIMESTAMP w/ tz | server default `now()` |
| `parsed_at` | TIMESTAMP w/ tz | nullable |

`UNIQUE (course_id, version)` constraint.

### Cascade rationale
`ON DELETE CASCADE` from `diseases → units` makes "replace all on re-upload" a single `DELETE FROM units WHERE course_id = ?` that wipes diseases too.

### Alembic
One new revision adds the `unit_status` enum and all three tables.

---

## 3. Parser — `app/services/disease_parser.py`

Pure functions, no I/O. Operates on `bytes` / `str` only.

```python
@dataclass
class ParsedDisease:
    name: str
    dsm_code: str | None
    category: str
    key_symptoms: list[str]
    differentials: list[str]
    difficulty_tier: int
    speech_style: str
    nudge_behavior: dict  # {frequency, tone, example}

@dataclass
class ParsedUnit:
    label: str
    diseases: list[ParsedDisease]

@dataclass
class ParseError:
    location: str   # "row 4" (CSV) or "units[0].diseases[2].dsm_code" (JSON)
    message: str

@dataclass
class ParseResult:
    units: list[ParsedUnit]
    errors: list[ParseError]
```

Functions:
- `parse_csv(text: str) -> ParseResult`
- `parse_json(text: str) -> ParseResult`
- `parse(filename: str, raw: bytes) -> ParseResult` — dispatches on extension; raises `ValueError` for unsupported extensions

### Required fields
- CSV columns (header row required): `unit_label, disease_name, dsm_code, category, key_symptoms, differentials, difficulty_tier, speech_style, nudge_frequency, nudge_tone, nudge_example`
- `key_symptoms` / `differentials` in CSV: semicolon-separated
- JSON: as in spec (`units[].diseases[]`)

### Validation rules
| Rule | On failure |
|------|------------|
| `name` non-empty | `ParseError` |
| `category` non-empty | `ParseError` |
| `key_symptoms` non-empty list | `ParseError` |
| `differentials` non-empty list | `ParseError` |
| `difficulty_tier` integer in `[1, 5]` | `ParseError` |
| `speech_style` non-empty | `ParseError` |
| `nudge_behavior.frequency` and `.tone` non-empty | `ParseError` |
| `unit_label` (CSV) / `units[].label` (JSON) non-empty | `ParseError` |
| `dsm_code` may be empty/null | OK |

Errors are **collected**, not raised — a partial file still produces `units` for the rows that parsed plus an `errors` list for the rows that didn't.

---

## 4. File storage — `app/services/file_storage.py`

Thin wrapper, ~20 lines.

```python
UPLOAD_ROOT = Path("/tmp/pocketpatient-uploads")

def save_upload(course_id: UUID, version: int, ext: str, raw: bytes) -> str:
    path = UPLOAD_ROOT / str(course_id) / f"{version}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return str(path)

def read_upload(file_url: str) -> bytes:
    return Path(file_url).read_bytes()

def upload_exists(file_url: str) -> bool:
    return Path(file_url).exists()
```

When we add GCS later, this is the single seam to change.

---

## 5. Endpoints — `app/routers/disease_documents.py`

Router prefix: `/courses/{course_id}/disease-document`, tag: `disease-documents`.

### `POST /courses/{course_id}/disease-document`
**Auth:** professor, must own the course
**Request:** `multipart/form-data` with a single file (`.csv` or `.json`)
**Behavior:**
1. Verify course exists and `current_user.id == course.professor_id`; else 404.
2. Read file bytes; detect extension. Unsupported → 400.
3. Compute next version: `MAX(version) + 1` for this course, starting at 1.
4. Save file to `/tmp/pocketpatient-uploads/{course_id}/{version}.{ext}`.
5. Insert `disease_documents` row (with `parsed_at = NULL`).
6. Parse file, build preview, return.

**Response (200):**
```json
{
  "document_id": "uuid",
  "version": 2,
  "units": [
    {"label": "Unit 1: Mood Disorders", "disease_count": 3, "diseases": ["MDD", "Bipolar I", "Bipolar II"]}
  ],
  "errors": [
    {"location": "row 5", "message": "missing required field: difficulty_tier"}
  ]
}
```

**Errors:**
- 400 — unsupported extension
- 401 — unauthenticated
- 403 — not a professor
- 404 — course not found OR caller is not the owner

### `POST /courses/{course_id}/disease-document/confirm`
**Auth:** professor, must own the course
**Behavior:**
1. Verify course ownership; else 404.
2. Find the latest `disease_documents` row for this course where `parsed_at IS NULL`. None → 404 ("no pending upload to confirm").
3. If the file no longer exists on disk → 410 ("Upload file expired, please re-upload").
4. Re-read and re-parse the file (don't trust cached parse).
5. If `parse_result.errors` is non-empty → 400 with the error list.
6. Check existing units for this course. If any have `status = 'released'` → 409 ("Close all released units before re-uploading").
7. `DELETE FROM units WHERE course_id = ?` (cascades to diseases).
8. Insert new units and diseases in a single transaction.
9. Set `parsed_at = now()` on the `disease_documents` row.
10. Return summary.

**Response (200):**
```json
{"units_created": 2, "diseases_created": 6, "document_id": "uuid", "version": 2}
```

**Errors:**
- 400 — parse errors present in file
- 401 — unauthenticated
- 403 — not a professor
- 404 — course not found OR no pending upload to confirm
- 409 — at least one existing unit is `released`
- 410 — upload file no longer on disk

### Notes
- Two unparsed documents could theoretically exist if the professor uploads twice without confirming. Confirm operates on the **latest** by `uploaded_at`; older unparsed rows are effectively abandoned. (Their files remain on disk; cleanup is out of scope for week 4.)
- `disease_documents` rows are never deleted, even when their file is gone — they're the audit trail. The file-missing case is handled by the 410 at confirm time.

---

## 6. Schemas — `app/schemas/`

### `app/schemas/disease_document.py`
- `UnitPreview` — `label: str`, `disease_count: int`, `diseases: list[str]`
- `ParseErrorOut` — `location: str`, `message: str`
- `DiseaseDocumentPreview` — `document_id: UUID`, `version: int`, `units: list[UnitPreview]`, `errors: list[ParseErrorOut]`
- `DiseaseDocumentConfirmResult` — `document_id: UUID`, `version: int`, `units_created: int`, `diseases_created: int`

(`Unit` and `Disease` read-schemas are NOT needed for week 4 — no endpoint returns them yet.)

---

## 7. Testing

### Pure parser tests — `tests/test_disease_parser.py`
- CSV happy path → all diseases parsed, no errors
- JSON happy path → all diseases parsed, no errors
- CSV with one bad row → other rows parse, error reported for the bad row
- JSON with one bad disease → other diseases parse, error reported for the bad path
- Each required-field validation rule has a dedicated test
- `difficulty_tier` out of range → error
- Unknown extension → `ValueError`
- Empty file → `ParseResult` with empty units, no errors

### Integration tests — `tests/test_disease_documents_router.py`
Marked with `pytestmark = pytest.mark.usefixtures("clean_tables")` like the existing routers.

- Upload happy path → 200, preview, document row created with `version=1` and `parsed_at=NULL`
- Upload with parse errors → 200, preview includes `errors[]`, units still parsed for good rows
- Upload unsupported extension → 400
- Upload by non-owner professor → 404
- Upload by student → 403
- Upload to non-existent course → 404
- Second upload increments version (1 → 2)
- Confirm happy path → 200, units + diseases created in DB, `parsed_at` set
- Confirm with no pending upload → 404
- Confirm with parse errors → 400, nothing created
- Confirm after manually deleting the file → 410
- Confirm with all existing units in `draft` status → replaces cleanly
- Confirm with one existing unit set to `released` → 409, nothing changes
- Re-upload after a confirmed import → second confirm replaces the data

### Sample fixture — `tests/fixtures/sample_diseases.json`
6 diseases across 2 units (e.g. Unit 1: Mood Disorders — MDD, Bipolar I, Bipolar II; Unit 2: Anxiety Disorders — GAD, Panic, Social Anxiety). Clinically reasonable values for `key_symptoms`, `differentials`, `nudge_behavior`. Used both by integration tests and by the joint week-04 manual test.

### Conftest updates
Add `units`, `diseases`, `disease_documents` to the TRUNCATE list in the `clean_tables` fixture.

---

## 8. Registration

```python
# app/main.py
from app.routers import auth, courses, disease_documents, enrollments, users
app.include_router(disease_documents.router, prefix="/api/v1")
```

```python
# app/models/__init__.py
from app.models.unit import Unit
from app.models.disease import Disease
from app.models.disease_document import DiseaseDocument
```

---

## 9. Out of scope for week 4

- GCS storage (file storage interface is the seam for later)
- Disease/unit read endpoints (no GET on units, diseases, or disease_documents yet)
- Unit status transitions (`draft` → `released` → `closed`)
- File cleanup on `/tmp` for abandoned uploads
- Streaming uploads for large files (week 4 expects small documents)
- Per-row preview of disease detail (preview returns counts + names only)
