#!/usr/bin/env bash
# Back up the PocketPatient dev database with pg_dump.
#
# Reads the connection string from $DATABASE_URL, falling back to the value in
# .env. Writes a timestamped custom-format dump to backups/.
#
# Usage:
#   ./scripts/backup_db.sh
#
# Restore (into an existing empty DB):
#   pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" backups/<file>.dump
#
set -euo pipefail

DB_URL="${DATABASE_URL:-}"
if [[ -z "$DB_URL" && -f .env ]]; then
  DB_URL="$(grep -E '^DATABASE_URL=' .env | head -1 | cut -d= -f2-)"
fi
if [[ -z "$DB_URL" ]]; then
  echo "DATABASE_URL not set and not found in .env" >&2
  exit 1
fi

# pg_dump speaks libpq URLs; strip the SQLAlchemy +asyncpg dialect suffix.
DB_URL="${DB_URL/+asyncpg/}"

mkdir -p backups
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="backups/pocketpatient-${STAMP}.dump"

pg_dump --format=custom --no-owner --file="$OUT" "$DB_URL"
echo "wrote $OUT"
