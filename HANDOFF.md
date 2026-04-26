# Anvil — Handoff (single-page brief)

> If you are a fresh Copilot CLI session, read THIS file first, then `~/.copilot/session-state/<id>/plan.md` if available.
> If you are a human picking this up later, same.

## What is Anvil
OSS image-annotation platform — V7 Darwin alternative — with built-in
SAM2 LoRA / full fine-tuning. Apache-2.0. Repo:
https://github.com/ssgaur/anvil-cv

The wedge vs V7: V7 ships frozen SAM2. Anvil lets you fine-tune SAM2
on your own data without leaving the labeling tool. Self-host or cloud.

## Two parallel tracks

### Track A — Launch Sprint (distribution, 2 weeks)
Goal: 3 director-level conversations + 1 pilot from a benchmark-driven launch.

Status: **Day 2-3 done.**
- `benchmarks/plantvillage/seed_data.py` — pulls 5k–50k color+segmented pairs from spMohanty mirror, derives binary masks. Smoke-tested.
- `benchmarks/plantvillage/benchmark_train.py` — 5 configs × 2 variants, hardware-agnostic. Smoke-tested on M1 Pro: vanilla 0.92 IoU, LoRA r8 (30 steps) 0.93. MPS works.
- `benchmarks/plantvillage/run_sweep.sh` — one-shot unattended runner.
- `benchmarks/plantvillage/VAST_RUNNER.md` — full Vast.ai recipe (~$5 budget).

Next (in this order):
1. **User runs `bash benchmarks/plantvillage/run_sweep.sh` overnight** on M1 Pro.
2. Commit `benchmarks/plantvillage/results/*.json` to repo.
3. User spins up 4 Vast.ai GPUs, runs same sweep there (~$5).
4. Agent writes `aggregate_results.py` → cost/time/IoU matrix PNG.
5. Agent writes blog post (markdown) with real numbers + git SHA receipts.
6. Agent writes static landing page (single HTML, github.io).
7. User decides domain name.
8. Agent drafts Show HN / Reddit / LinkedIn outreach copy.
9. User launches + runs 50-person cold outreach.
10. Day 14 review: build/sell/kill decision.

Track A folder anchor: **`benchmarks/`, `docs/launch/`, landing page repo.** Do NOT touch `apps/`.

### Track B — Product Hardening (ongoing)
Goal: someone outside us can `docker compose up` Anvil on Monday and feel it's enterprise-grade.

Status: **Stack works end-to-end.** All containers Healthy, CI green (3 jobs: python/web/docker-smoke). Annotation core, classes, projects, datasets, COCO import/export, SAM2 click-prompt, LoRA training, A/B compare, per-class IoU, review flow all wired.

Open hardening todos (in SQL `todos` table, prefix `hardening-*`):
- `hardening-auth` — real JWT auth (currently single hardcoded dev user)
- `hardening-multiuser` — admin/labeler/reviewer roles
- `hardening-onboarding` — first-run /setup wizard
- `hardening-error-states` — empty states, ML-down states across UI
- `hardening-windows` — verify Windows Docker Desktop + bare Linux
- `hardening-keyboard` — V7-parity shortcuts (B/P/Z/space/1-9/ctrl+z)
- `hardening-perf` — pagination + virtualized list for 10k+ image projects
- `hardening-backup` — `anvil-cli backup/restore`
- `hardening-observability` — `/metrics` Prometheus, JSON logs, Sentry hook

Plus pre-existing pending: `annotation-canvas`, `docs-site`, `quality-dashboard`, `public-demo`, `phase2-video-finetune`, `finalize-name-domain`.

Track B folder anchor: **`apps/`, `compose.yml`, root configs.** Do NOT touch `benchmarks/`.

## Cross-track rules
- Same repo, different folders.
- Two **separate Copilot CLI sessions** going forward (one per track) — keeps each session under ~5 checkpoints, faster + fewer mistakes.
- Both branches off `main`. Merge to main directly (solo dev). CI must stay green.

## Critical gotchas (preserved across summaries — DO NOT forget)
- Never add `from __future__ import annotations` to `apps/api/anvil_api/models.py` (breaks SQLModel relationships).
- React-Konva needs explicit `type` imports.
- `load_lora()` already calls `inject_lora()` — never call both.
- Bash refuses `kill -9 $VAR` and `pkill`/`killall`.
- Vite dev needs stdin redirect (`</dev/null`) under nohup.
- Postgres-only SQL (JSONB, ALTER TABLE … IF NOT EXISTS).
- ReviewStatus state machine: `new → in_progress → submitted → approved/rejected`, with `reopen` from approved/rejected → in_progress.
- Same user can approve their own work (no roles yet — `hardening-multiuser`).
- Vendored `vendor/sam2/training/dataset/` is **video-only** (VOSDataset/PNGRawDataset). For image fine-tune, use our own `apps/ml/anvil_ml/training.py`.
- PlantVillage segmented files use lowercase `.jpg` + `_final_masked` suffix (vs uppercase `.JPG` for color).
- 256×256 native — direct bicubic resize to 1024×1024, NOT letterbox (letterbox gave only 5% leaf coverage).
- M1 Pro 16GB: full fine-tune with `base_plus` will OOM. Use `--variant tiny` or skip.

## Latest known-good commit on `main`
`a546a5b feat(benchmark): one-shot run_sweep.sh wrapper`

## Starting a new session — paste this
> "Read `~/PersonalDev/anvil-cv/HANDOFF.md`. We are working on Track [A or B] only. Don't touch the other track's folders unless I explicitly ask. Then check `todos` table in SQL for current pending items in this track."
