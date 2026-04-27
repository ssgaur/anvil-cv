#!/usr/bin/env bash
# Anvil restore from a backup produced by scripts/backup.sh.
# WARNING: this destroys the existing Postgres DB and overwrites the MinIO
# bucket. Designed for disaster recovery / dev clones, not partial restores.
#
# Usage:
#   scripts/restore.sh path/to/backup.tar.gz
#
# Requires: backup format v1 (manifest.json + db.sql + storage/).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 path/to/backup.tar.gz" >&2
  exit 2
fi
SRC="$1"
[[ -f "$SRC" ]] || { echo "not found: $SRC" >&2; exit 1; }

PG_USER="${POSTGRES_USER:-anvil}"
PG_DB="${POSTGRES_DB:-anvil}"
BUCKET="${STORAGE_BUCKET:-anvil}"
MINIO_USER="${MINIO_ROOT_USER:-anvil}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:-anvilanvil}"

if [[ "${ANVIL_RESTORE_FORCE:-}" != "1" ]]; then
  read -r -p "This will DESTROY DB '$PG_DB' and overwrite bucket '$BUCKET'. Type YES to continue: " ok
  [[ "$ok" == "YES" ]] || { echo "aborted"; exit 1; }
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[1/4] extract $SRC"
tar -C "$WORK" -xzf "$SRC"
[[ -f "$WORK/db.sql" && -d "$WORK/storage" ]] || { echo "invalid backup (missing db.sql or storage/)" >&2; exit 1; }

echo "[2/4] reset Postgres database '$PG_DB'"
docker compose exec -T postgres psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid)
  FROM pg_stat_activity
 WHERE datname = '$PG_DB' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "$PG_DB";
CREATE DATABASE "$PG_DB" OWNER "$PG_USER";
SQL

echo "[3/4] restore SQL dump"
docker compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 < "$WORK/db.sql" >/dev/null

echo "[4/4] restore MinIO bucket '$BUCKET'"
docker run --rm \
  --network anvil-cv_default \
  -v "$WORK/storage:/in" \
  --entrypoint /bin/sh \
  minio/mc:latest -c \
  "mc alias set local http://minio:9000 '$MINIO_USER' '$MINIO_PASS' >/dev/null && \
   mc mb -p local/'$BUCKET' >/dev/null 2>&1 || true && \
   mc mirror --quiet --overwrite --remove /in local/'$BUCKET'"

echo "OK. Restored from $SRC. Restart the API: docker compose restart api"
