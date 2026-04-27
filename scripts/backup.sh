#!/usr/bin/env bash
# Anvil dev/prod backup. Streams a Postgres dump + the MinIO bucket into a
# single .tar.gz file. Designed to be run on the same host as docker compose
# (it shells into the postgres + minio-mc containers — no extra deps).
#
# Usage:
#   scripts/backup.sh [output.tar.gz]
#
# Defaults to ./backups/anvil-YYYYMMDD-HHMMSS.tar.gz.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OUT="${1:-./backups/anvil-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}"
mkdir -p "$(dirname "$OUT")"

PG_USER="${POSTGRES_USER:-anvil}"
PG_DB="${POSTGRES_DB:-anvil}"
BUCKET="${STORAGE_BUCKET:-anvil}"
MINIO_USER="${MINIO_ROOT_USER:-anvil}"
MINIO_PASS="${MINIO_ROOT_PASSWORD:-anvilanvil}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/storage"

echo "[1/3] pg_dump → $WORK/db.sql"
docker compose exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" --no-owner --no-privileges \
  > "$WORK/db.sql"

echo "[2/3] mirror MinIO bucket '$BUCKET' → $WORK/storage"
# Use a one-shot mc container that shares the docker network so it can resolve `minio`.
docker run --rm \
  --network anvil-cv_default \
  -v "$WORK/storage:/out" \
  --entrypoint /bin/sh \
  minio/mc:latest -c \
  "mc alias set local http://minio:9000 '$MINIO_USER' '$MINIO_PASS' >/dev/null && \
   mc mirror --quiet --overwrite local/'$BUCKET' /out"

cat > "$WORK/manifest.json" <<EOF
{
  "version": 1,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "postgres_db": "$PG_DB",
  "storage_bucket": "$BUCKET"
}
EOF

echo "[3/3] tar → $OUT"
tar -C "$WORK" -czf "$OUT" manifest.json db.sql storage

SIZE=$(du -h "$OUT" | cut -f1)
echo "OK. Backup written: $OUT ($SIZE)"
