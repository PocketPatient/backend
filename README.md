# PocketPatient Backend

FastAPI backend for PocketPatient. Uses PostgreSQL, Redis, and Firebase Auth.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — `brew install uv`
- Docker (for local Postgres + Redis)

## Setup

```bash
# Install dependencies
uv sync

# Start Postgres and Redis
docker compose up -d

# Copy and fill in env vars
cp .env.example .env
```

## Running

```bash
uv run uvicorn app.main:app --reload
```

API available at `http://localhost:8000`. Docs at `http://localhost:8000/docs`.

## Database Migrations

```bash
# Apply migrations
uv run alembic upgrade head

# Create a new migration
uv run alembic revision --autogenerate -m "description"
```

## Testing

```bash
uv run pytest
```

## Dependency Management

```bash
# Add a dependency
uv add <package>

# Add a dev dependency
uv add --dev <package>

# Update lockfile
uv sync
```
