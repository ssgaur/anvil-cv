#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Complete docs/DEV_SETUP.md first." >&2
  exit 1
fi
if [[ ! -f .anvil-personal/PROPAINTER_PERSONAL_USE_ONLY ]]; then
  echo "Personal ProPainter backend is not installed." >&2
  echo "Run: python3 scripts/install_propainter_personal.py --accept-noncommercial-license" >&2
  exit 1
fi

echo "[1/3] Starting Anvil containers..."
docker compose up -d

echo "[2/3] Waiting for API and ML..."
for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1 \
    && curl -fsS http://127.0.0.1:8001/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS http://127.0.0.1:8000/ready >/dev/null
curl -fsS http://127.0.0.1:8001/health >/dev/null

echo "[3/3] Starting native MPS worker with sleep prevention..."
echo "Open http://localhost:5173/model"
echo "Interrupted jobs will NOT auto-run. Click Resume job in the UI."
exec caffeinate -dimsu "$ROOT/.venv/bin/python" "$ROOT/scripts/personal_video_worker.py"
