# PocketPatient Backend

FastAPI + SQLAlchemy 2.0 async + Postgres backend for a psychiatry-training app where students message AI "patients" assigned by their professor.

## Stack & tooling
- Python 3.11+, `uv` for everything (`uv run pytest`, `uv run alembic ...`, `uv run uvicorn app.main:app --reload`)
- SQLAlchemy 2.0 async with `Mapped[]` / `mapped_column`, `asyncpg` driver
- Pydantic v2 for schemas
- pytest + pytest-asyncio (`asyncio_mode=auto`), httpx `AsyncClient` for integration tests
- Auth: RS256 JWT in `Authorization: Bearer`, verified in `app/deps.py`

## Layout
- `app/models/` — SQLAlchemy models (one per table, re-exported in `__init__.py`)
- `app/schemas/` — Pydantic request/response models
- `app/routers/` — FastAPI routers, registered in `app/main.py` with `/api/v1` prefix
- `app/services/` — Pure helpers (parsers, file storage, etc.)
- `app/deps.py` — `get_current_user`, `require_role("professor"|"student")`
- `alembic/versions/` — Migrations (autogenerate works; verify cascade/check constraints by hand)
- `tests/` — Integration tests use `clean_tables` fixture + `professor`/`student` fixtures from `conftest.py`
- `docs/api-contract.md` — Canonical endpoint reference
- `docs/superpowers/specs/` and `docs/superpowers/plans/` — Per-week design + implementation plans

## Databases
- Dev: `pocketpatient` (URL in `.env` / `alembic.ini`)
- Test: `pocketpatient_test` (must exist; tests create/drop tables via `Base.metadata`, NOT alembic)

## Key conventions
- Routers do auth via `Depends(require_role(...))`; ownership checks return **404** (not 403) to avoid leaking existence
- Tests follow TDD: write failing test → minimal impl → green → commit
- File storage is local `/tmp/pocketpatient-uploads/` for dev — GCS seam is `app/services/file_storage.py`
- Class codes are 6-char uppercase, no ambiguous chars (0/O/1/I/L)

## Common commands
```bash
uv run pytest -v                                    # full suite
uv run pytest tests/test_<name>.py -v               # one file
uv run alembic revision --autogenerate -m "..."     # new migration
uv run alembic upgrade head                         # apply
uv run uvicorn app.main:app --reload                # dev server
```
