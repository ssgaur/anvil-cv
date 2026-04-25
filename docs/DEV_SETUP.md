# Local Dev Setup

This is the **3-process recipe** the maintainer uses on M1 macOS. It's the fastest path to a working dev environment until `docker-compose-dev` ships.

## Prereqs

- macOS (M1/M2/M3) — Linux works too with minor changes
- Python 3.11
- Node 20 + npm
- Postgres 15+ (Homebrew: `brew install postgresql@15 && brew services start postgresql@15`)
- Redis (only if you want the worker; optional in dev)
- ~10 GB disk (SAM2 base checkpoint is ~700 MB)

## One-time bootstrap

```bash
# 1. Clone
git clone https://github.com/ssgaur/anvil-cv.git
cd anvil-cv

# 2. Create the database
createdb anvil

# 3. Python env
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip uv
uv sync # installs api, ml, worker, storage deps

# 4. Web deps
cd apps/web && npm install && cd ../..

# 5. SAM2 checkpoint
mkdir -p checkpoints
curl -L -o checkpoints/sam2.1_hiera_base_plus.pt \
 https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt

# 6. Env
cp .env.example .env
# Edit .env: set DATABASE_URL=postgresql+psycopg://<you>@localhost:5432/anvil
# set SAM2_CHECKPOINT=./checkpoints/sam2.1_hiera_base_plus.pt
# set SAM2_MODEL_CFG=configs/sam2.1/sam2.1_hiera_b+.yaml

# 7. Migrate (auto on api boot)
```

## Run (every session)

```bash
cd ~/PersonalDev/anvil-cv
source .venv/bin/activate
set -a && source .env && set +a
mkdir -p logs

# Kill stale
lsof -ti:8000 -ti:8001 -ti:5173 | xargs kill 2>/dev/null

# ML first (slow boot ~3s)
(cd apps/ml && PYTHONPATH=$PWD nohup python -m uvicorn anvil_ml.main:app --host 127.0.0.1 --port 8001 </dev/null > ~/PersonalDev/anvil-cv/logs/ml.log 2>&1 & disown)

# API
(cd apps/api && PYTHONPATH=$PWD nohup python -m uvicorn anvil_api.main:app --host 127.0.0.1 --port 8000 </dev/null > ~/PersonalDev/anvil-cv/logs/api.log 2>&1 & disown)

# Web
(cd apps/web && nohup npm run dev </dev/null > ~/PersonalDev/anvil-cv/logs/web.log 2>&1 & disown)

# Watch
tail -f logs/*.log
```

Open <http://localhost:5173>. First account you create is yours.

## Worker (optional)

```bash
(cd apps/worker && PYTHONPATH=$PWD nohup python -m anvil_worker </dev/null > ~/PersonalDev/anvil-cv/logs/worker.log 2>&1 & disown)
```

Without the worker, embedding pre-compute is skipped → click-to-segment will fall back to a slow path that runs the encoder on demand. Fine for dev with a few images.

## Linters & tests

```bash
# Python
ruff check .
mypy apps/api/anvil_api apps/ml/anvil_ml || true # mypy is lenient

# Web
cd apps/web && npm run typecheck && npm run lint

# E2E (services must be running)
python scripts/e2e_smoketest.py
python scripts/sam2_smoketest.py
python scripts/finetune_e2e.py
python scripts/model_versions_e2e.py
python scripts/review_flow_e2e.py
python scripts/class_properties_e2e.py
python scripts/coco_roundtrip_e2e.py
```

## Common errors

| Symptom | Fix |
| --- | --- |
| `connection refused` on `:8001` from API | ML didn't boot — `tail logs/ml.log`, usually missing checkpoint or wrong CFG path |
| Model versions tab blank | Run `npm run typecheck` — usually a missing `type` import (gotcha #2) |
| Training stuck epoch 0 | Worker isn't running → embeddings missing → check `data/embeddings/` |
| `relation "user" does not exist` | DB never migrated; restart API and check logs for SQLModel `create_all` |
| Vite dies immediately | You forgot `</dev/null` in the launch command |

See `.github/copilot-instructions.md` for the full gotchas list.
