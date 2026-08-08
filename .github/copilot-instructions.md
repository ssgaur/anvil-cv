# Copilot Onboarding for Anvil

> **Read this first** when starting a fresh Copilot CLI / Coding Agent session on this repo. It contains everything you need to be productive without scanning the whole codebase.
>
> **For current state + open todos + which track you should be on, also read [`COPILOT_HANDOFF.md`](COPILOT_HANDOFF.md) and [`HANDOFF.md`](../HANDOFF.md).** This file (`copilot-instructions.md`) is the *stable* onboarding (architecture, conventions, gotchas). COPILOT_HANDOFF.md and HANDOFF.md are the *live* state (what's done, what's next, which folders to touch).

## Work is split into TWO tracks — pick one per session

To keep sessions short and focused (avoid 11-checkpoint sprawl), Anvil work runs as two parallel tracks. **A session should commit to ONE track and not touch the other track's folders.**

| Track | Goal | Folder anchor (yours to touch) | Folder anchor (DO NOT touch) |
|---|---|---|---|
| **A — Launch Sprint** | Distribution sprint: SAM2 fine-tuning benchmark → blog → landing → outreach | `benchmarks/`, `docs/launch/`, landing page repo | `apps/`, `compose.yml` |
| **B — Product / OSS App** | Make the OSS app feel enterprise-grade: annotation UX, auth, roles, onboarding, docs, launch readiness | `apps/`, `compose.yml`, root configs, `.github/` | `benchmarks/` |

If the user hasn't told you which track, **ask before touching code**. Cross-track changes are allowed only when explicitly requested.

## TL;DR — what is this?

**Anvil** is an OSS image-annotation platform (V7 Darwin / CVAT competitor) with one differentiator: **built-in SAM2 LoRA fine-tuning** that runs on a single M1 16 GB laptop. The owner (`ssgaur`) is a solo dev building this as an enterprise product targeting computer-vision teams (e.g., retail produce defect detection at Walmart).

Stack: **FastAPI + Postgres + SQLModel** (api), **FastAPI + SAM2 + PyTorch** (ml), **Vite + React + TS + Konva** (web), **Arq + Redis** (worker), pluggable storage (local default).

## Repo map

```
anvil-cv/
├── apps/
│ ├── api/anvil_api/ FastAPI REST API
│ │ ├── main.py app entrypoint, includes routers
│ │ ├── models.py SQLModel tables (User, Project, Dataset, Image,
│ │ │ Annotation, Class, ClassProperty, ModelVersion,
│ │ │ Comment, ReviewStatus enum)
│ │ ├── schemas.py Pydantic request/response (XxxIn / XxxOut)
│ │ ├── ml_client.py HTTP client to ML service (predict/train/evaluate)
│ │ ├── auth.py / db.py / config.py / storage.py
│ │ └── routes/ annotations, auth, bulk, classes, comments, datasets,
│ │ images, models, projects, setup
│ ├── ml/anvil_ml/ FastAPI ML service (port 8001)
│ │ ├── main.py /predict, /train, /evaluate endpoints
│ │ ├── engine.py SAM2 model loader + cached predictor
│ │ ├── lora.py LoRA inject/save/load (~50 lines)
│ │ └── training.py train_lora() loop + _evaluate() + evaluate_with_adapter()
│ ├── web/src/
│ │ ├── pages/ LoginPage, ProjectsPage, ProjectDetailPage,
│ │ │ DatasetDetailPage, WorkviewPage
│ │ ├── components/ app shell, onboarding tour, guards, toasts
│ │ ├── lib/api.ts typed REST client
│ │ ├── store/ zustand stores
│ │ └── types/index.ts shared TS types
│ └── worker/anvil_worker/ Arq tasks (embed images at upload time)
├── packages/storage/ Pluggable storage providers
├── scripts/ One E2E smoke test per major feature
│ ├── e2e_smoketest.py full project create + image upload + annotate
│ ├── sam2_smoketest.py SAM2 predict
│ ├── finetune_e2e.py LoRA train
│ ├── model_versions_e2e.py per-class IoU + A/B compare + delete
│ ├── review_flow_e2e.py approve/reject/comments
│ ├── class_properties_e2e.py single/multi/text attributes
│ ├── coco_roundtrip_e2e.py export then re-import
│ ├── undo_redo_e2e.py / bbox_resize_e2e.py
│ ├── auth_e2e.py / prod_features_e2e.py
│ └── load_sample_dataset.py synthetic launch demo loader
├── data/ local Postgres + uploads dir (gitignored)
├── logs/ api.log / ml.log / web.log / worker.log
├── checkpoints/ SAM2 base weights (gitignored)
├── docs/ ARCHITECTURE, FEATURES, FINE_TUNING, API, DEV_SETUP, FAQ, ROADMAP
│ ├── assets/anvil-demo.gif launch visual
│ └── launch/DEMO_RECORDING.md
├── mkdocs.yml docs site config
└── .github/ this file + CI/docs/GHCR workflows + issue/PR templates
```

## Current status (as of latest commit)

- **50 / 61 tracked todos done.** 11 product backlog items remain:
  active-learning queue, API tokens, audit log, bundle/model export, YOLO export,
  comments/mentions, quality dashboard, S3 backend, task assignment, webhooks,
  Windows verification.
- Product hardening is complete: RBAC, HttpOnly cookies, refresh-token rotation,
  password reset, setup wizard, CSRF, rate limiting, observability, backup/restore.
- Annotation product phase 1 is complete: polyline, keypoints, OBB, bulk ops,
  class hierarchy, server-side image filters/sort, COCO export/import
  extensions, model versions, comments, and review flow.
- Recent Workview polish is complete: visible brush cursor, selection exits
  tools into edit mode, clearer polygon/polyline behavior, OBB creation/edit UX,
  and remaining browser confirms replaced with in-app dialogs.
- OSS launch readiness is complete: README, demo GIF, sample loader, community
  files, MkDocs/GitHub Pages workflow, GHCR workflow, onboarding tour, keyboard help.
- Current strategic direction: paid/Pro team review workflows + Model Studio
  fine-tuning, gated behind OSS/Pro feature flags. See
  [docs/PAID_PRODUCT_ROADMAP.md](../docs/PAID_PRODUCT_ROADMAP.md).
- Latest known-good state: `main` at or after
  `cbdfa2d fix(web): replace remaining browser confirms with app dialogs`.
- Source of truth: [COPILOT_HANDOFF.md](COPILOT_HANDOFF.md),
  [HANDOFF.md](../HANDOFF.md), [docs/PAID_PRODUCT_ROADMAP.md](../docs/PAID_PRODUCT_ROADMAP.md),
  plus the SQL `todos` table.

## Local dev — start the app

Preferred OSS path:

```bash
cp .env.example .env
docker compose up -d --build
python3 scripts/load_sample_dataset.py
```

Open <http://localhost:5173>. Dev admin is `admin@anvil.dev` / `admin`.

### Local 3-process recipe

API on :8000, ML on :8001, web on :5173. Postgres expected on localhost:5432 (database `anvil`).

```bash
# kill anything stale on those ports
lsof -ti:8000 -ti:8001 -ti:5173 | xargs kill 2>/dev/null

cd ~/PersonalDev/anvil-cv && source .venv/bin/activate
set -a && source .env && set +a

# ML first (api depends on it being up)
(cd apps/ml && PYTHONPATH=$PWD nohup python -m uvicorn anvil_ml.main:app --host 127.0.0.1 --port 8001 </dev/null > ~/PersonalDev/anvil-cv/logs/ml.log 2>&1 & disown)
(cd apps/api && PYTHONPATH=$PWD nohup python -m uvicorn anvil_api.main:app --host 127.0.0.1 --port 8000 </dev/null > ~/PersonalDev/anvil-cv/logs/api.log 2>&1 & disown)
(cd apps/web && nohup pnpm dev </dev/null > ~/PersonalDev/anvil-cv/logs/web.log 2>&1 & disown)
```

Then `tail -f logs/*.log` and open <http://localhost:5173>.

Full setup including Postgres bootstrap: [docs/DEV_SETUP.md](../docs/DEV_SETUP.md).

## Critical gotchas (preserve these — earned through pain)

1. **Never add `from __future__ import annotations` to `apps/api/anvil_api/models.py`.** It silently breaks SQLModel relationship resolution. Other modules (e.g. `routes/comments.py`) use it fine because they have no SQLModel relationships.
2. **React-Konva + Vite TS:** `FormEvent`, `ReactNode`, etc. need an explicit `type` import: `import type { FormEvent, ReactNode } from "react"`. Otherwise the Models tab (and other complex components) won't render.
3. **`load_lora()` already calls `inject_lora()` internally.** Calling both wraps Linear layers twice → silently broken on the second adapter load. See `apps/ml/anvil_ml/training.py::evaluate_with_adapter`.
4. **Bash refuses `kill -9 $VAR`** in this environment. Read the PID first then call `kill <literal_number>`. Likewise no `pkill` / `killall`.
5. **Vite dev must redirect stdin** (`</dev/null`) when launched via nohup or it dies trying to read stdin.
6. **Postgres-only SQL:** the codebase uses `JSONB`, `ALTER TABLE … ADD COLUMN IF NOT EXISTS`, etc. SQLite will not work as a drop-in.
7. **MPS backward is flaky** for some attention ops in PyTorch 2.x. `TrainConfig.device` defaults to `"cpu"` for that reason. Inference can still use MPS via `engine.py`.
8. **Encoder cache lives on disk** as `embeddings/<image_id>.npz`. The worker writes them at upload time. Training reads them via `_restore_features(predictor, path)` so the encoder never runs at training time — that's why LoRA is fast on M1.
9. **Adapter inference is cached** in `_adapter_predictors` dict in `training.py`, keyed by adapter path. The `/compare` endpoint deliberately builds fresh predictors each call — don't "optimize" it to use the cache or A/B numbers will be stale.
10. **Class properties** (`apps/api/anvil_api/models.py::ClassProperty`) come in three flavors via `ClassPropertyType` enum: `single`, `multi`, `text`. Annotation `properties` is a JSONB blob keyed by property id.
11. **Review approval/rejection blocks self-review.** Reviewer/admin can approve or reject submitted work, but the submitter cannot review their own image.
12. **Do not add browser-native popups.** Use `ConfirmDialog` for destructive confirms and toast/error UI for status messages.

## Coding conventions

- **Commits:** Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`). Scope in parens: `feat(api,web): …`. Always include `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` trailer.
- **Python:** ruff + mypy (lenient). `uv` for dep management. SQLModel for ORM. Async FastAPI. Type hints required on public functions.
- **TypeScript:** strict mode, explicit `type` imports. `pnpm` (lockfile pinned via `packageManager` in `apps/web/package.json`). Zustand for state. Konva for canvas.
- **Naming:** `XxxIn` for create requests, `XxxOut` for responses, `XxxUpdate` for PATCH bodies.
- **Endpoints:** RESTful, plural collections (`/api/projects/{id}/datasets`). All endpoints under `/api/`. Prefer 204 No Content for deletes.
- **Tests:** add a `scripts/<feature>_e2e.py` for any new vertical feature; existing ones are the reference pattern (login → create project → upload image → exercise feature → assert).
- **Comments:** only when code needs clarification; avoid narrating obvious behavior.

## How to add a feature (the well-trodden path)

1. **Schema:** add table / columns in `apps/api/anvil_api/models.py`. Use `JSONB` for flexible blobs.
2. **Pydantic:** add `XxxIn` / `XxxOut` in `apps/api/anvil_api/schemas.py`.
3. **Route:** new file or extend existing in `apps/api/anvil_api/routes/`. Wire in `main.py`.
4. **ML (if needed):** extend `apps/ml/anvil_ml/main.py` + matching method in `apps/api/anvil_api/ml_client.py`.
5. **Frontend types:** mirror in `apps/web/src/types/index.ts`.
6. **API client:** add function in `apps/web/src/lib/api.ts`.
7. **UI:** new component / page. Common patterns live in `apps/web/src/components/`.
8. **E2E test:** `scripts/<feature>_e2e.py` that exercises the full slice.
9. **Restart services**, run the E2E, then commit.

## Common debugging recipes

**Run `python3 scripts/diagnose.py` first.** It reports container OOM kills,
service health, memory pressure, failed images, and recent incidents — each with
the exact command to fix it. Use `--json` for machine-readable output. For the
cumulative cross-container log, run `python3 scripts/logcollect.py` and read
`logs/anvil.jsonl`. Full details: [docs/OBSERVABILITY.md](../docs/OBSERVABILITY.md).

- **API 500?** `tail -100 logs/api.log` — most issues are SQLModel relationship errors (gotcha #1) or missing ML service.
- **ML predict slow?** First call after restart is slow (model warm-up). Subsequent calls hit the cached predictor.
- **Web shows stale data?** `apps/web/src/lib/api.ts` doesn't cache; check React Query / Zustand store invalidation in the relevant page component.
- **Training stuck at epoch 0?** Almost always missing embedding cache → check worker ran and `embeddings/*.npz` exist for the dataset.
- **A/B compare numbers identical?** Check both adapter paths exist; null adapter side = base SAM2.

## Where to learn more

- [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) — request flow, encoder cache, training loop
- [docs/FINE_TUNING.md](../docs/FINE_TUNING.md) — LoRA strategy, comparison vs Meta's full FT
- [docs/API.md](../docs/API.md) — every endpoint
- [docs/FEATURES.md](../docs/FEATURES.md) — UI features + shortcuts
- [docs/PAID_PRODUCT_ROADMAP.md](../docs/PAID_PRODUCT_ROADMAP.md) — paid/Pro review workflow + fine-tuning plan
- [docs/OBSERVABILITY.md](../docs/OBSERVABILITY.md) — diagnose script, cumulative log, incident codes
- [docs/FAQ.md](../docs/FAQ.md) — common user questions
- [.github/COPILOT_HANDOFF.md](COPILOT_HANDOFF.md) — current product/session handoff
- `plan/latest.md` — author's working notes (may be stale; trust the code)

## Session-state memory (for Copilot CLI specifically)

The author runs Copilot CLI long sessions. Session state lives at `~/.copilot/session-state/<uuid>/plan.md` with checkpoints in `checkpoints/`. **Always read `plan.md` first** when resuming, then read `.github/COPILOT_HANDOFF.md` and check the latest checkpoint title for the most recent feature shipped.
