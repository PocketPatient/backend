# PocketPatient Backend

FastAPI backend for PocketPatient. Uses PostgreSQL, Redis, and Firebase Auth.

## Requirements

- Python 3.11+
- Docker (for local Postgres + Redis)

## Setup

### 1. Create a virtual environment and install uv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install uv
```

### 2. Install project dependencies

```bash
uv sync
```

### 3. Start Postgres and Redis

```bash
docker compose up -d
```

### 4. Configure environment variables

```bash
cp .env.example .env
# Fill in the values in .env
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
