# Local Dev Setup

Two ways to run Anvil locally:

- **Docker (one command, recommended for users / kicking the tires):** `docker compose up -d --build` from the repo root (after `cp .env.example .env`). See the README quick-start section. Everything runs in containers; no Python or Node install needed on your host. *(If you have `make` installed, `make up` does the same thing.)*
- **3-process bare-metal (recommended for hacking on Anvil itself):** the rest of this doc. Faster iteration, no Docker overhead, native MPS for ML.

## Bare-metal 3-process setup

This is the **3-process recipe** the maintainer uses on M1 macOS.

## Prereqs

- macOS (M1/M2/M3) — Linux works too with minor changes
- Python 3.11
- Node 20 + pnpm 9 (`corepack enable` then `corepack prepare pnpm@9.12.3 --activate`)
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
cd apps/web && pnpm install --frozen-lockfile && cd ../..

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
(cd apps/web && nohup pnpm dev </dev/null > ~/PersonalDev/anvil-cv/logs/web.log 2>&1 & disown)

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
cd apps/web && pnpm typecheck && pnpm lint

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

## Authentication

Anvil ships with three login paths, all minted as the same JWT:

1. **Email + password** (default).
2. **Google OAuth** — enable by setting `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET`.
3. **GitHub OAuth** — enable by setting `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET`.

The login screen automatically shows only the providers configured on the
backend (it queries `GET /api/auth/providers`).

### Bootstrap admin

On first boot, Anvil seeds an admin from `ANVIL_ADMIN_EMAIL` /
`ANVIL_ADMIN_PASSWORD` (defaults `admin@anvil.dev` / `admin` for dev). Set
`ANVIL_ADMIN_BOOTSTRAP=false` to skip auto-creation entirely (use OAuth or
`/api/auth/signup` instead).

If a user with `ANVIL_ADMIN_EMAIL` already exists with a non-admin role, the
bootstrap idempotently **promotes** them to admin on every API start
(break-glass mechanism — keep the env var unset in production after initial
setup if you don't want this).

### Roles (RBAC)

Single global role per user. Hierarchy (most → least privileged):

| Role     | Can do                                                                |
| -------- | --------------------------------------------------------------------- |
| admin    | Manage users (`/admin/users`), create/delete projects, train/delete models |
| reviewer | Manage datasets/classes; approve/reject submissions; set active model |
| labeler  | Annotate, comment, submit images for review                           |
| viewer   | Read-only across the instance                                         |

Key invariants:

- New signups default to **viewer** (configure with `ANVIL_DEFAULT_SIGNUP_ROLE`).
  Admins must promote users via `/admin/users` (UI: sidebar → **Users**).
- Reviewers cannot approve/reject their **own submissions** (returns `409`).
- Annotation/comment writes are **locked** when an image is `submitted` or
  `approved`. A reviewer must `reopen` it first.
- File downloads use **HMAC-signed URLs** (`?exp=&sig=`, 1 h expiry) so
  `<img src>` works without an Authorization header.
- The last admin cannot be demoted or deleted (`SELECT FOR UPDATE` race-safe).
- Hard-deleting a user is blocked while they have FK references (projects,
  annotations, comments, model versions, images) — reassign or remove those
  first.

### Production guardrails

When `ANVIL_ENV=production`, the API refuses to start if:

- `JWT_SECRET` is the default `change-me` or shorter than 32 characters, or
- `ANVIL_ADMIN_BOOTSTRAP=true` (default) and `ANVIL_ADMIN_PASSWORD` is the
  default `admin` (or empty).

Generate a strong secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

### Sign-up gating

Set `ALLOW_SIGNUP=false` in `.env` to disable `/api/auth/signup` (returns
`403`). The login page also hides the "Create account" toggle when signup is
disabled. Existing accounts (and OAuth) keep working.

### Setting up Google OAuth

1. Google Cloud Console → APIs & Services → Credentials → **Create OAuth
   client ID** → Web application.
2. **Authorized redirect URI**: `http://localhost:8000/api/auth/oauth/google/callback`
   (use your `OAUTH_REDIRECT_BASE` for non-local).
3. Copy client id + secret into `.env`:
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   ```
4. Restart the API. The "Continue with Google" button appears.

### Setting up GitHub OAuth

1. GitHub → Settings → Developer settings → **OAuth Apps** → **New OAuth App**.
2. **Authorization callback URL**: `http://localhost:8000/api/auth/oauth/github/callback`.
3. Copy client id + secret into `.env`:
   ```
   GITHUB_CLIENT_ID=...
   GITHUB_CLIENT_SECRET=...
   ```
4. Restart the API.

### Account linking rules

When a user signs in with a provider whose verified email matches an existing
local account, the OAuth identity is linked to that account automatically.
Anvil only links by **provider-verified** email:

- Google: requires `email_verified == true` from the userinfo endpoint.
- GitHub: requires a `primary && verified` (or any verified) email from
  `GET /user/emails`.

Unverified emails are rejected with a redirect back to `/login?oauth_error=...`.
