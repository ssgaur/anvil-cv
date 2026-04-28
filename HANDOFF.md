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

### Track B — Product Hardening (DONE — v0 hardening complete)
Goal: someone outside us can `docker compose up` Anvil on Monday and feel it's enterprise-grade.

Status: **All 11 hardening items shipped to `main`.** Stack works end-to-end, all containers Healthy, CI green (3 jobs: python/web/docker-smoke). Auth is now production-shaped: HttpOnly cookie sessions + bearer-header CLI path, RBAC (admin/reviewer/labeler/viewer), GitHub OAuth, rotating refresh tokens with reuse detection, password-reset flow, CSRF middleware, in-process rate limiting, /metrics + JSON logs + /ready, `anvil-cli backup/restore`, first-run /setup wizard, V7-parity keyboard shortcuts, paginated/virtualized image list, empty + ML-down states.

Closed hardening todos (in SQL `todos` table):
- `hardening-auth` → `176197c`
- `hardening-multiuser` (RBAC) → `9e95b47`
- `hardening-rate-limit` → `6a01137`
- `hardening-onboarding` (/setup) → `3559026`
- `hardening-error-states` (toasts) → `3971a49`
- `hardening-keyboard` → `2920fa4`
- `hardening-perf` (pagination) → `6e790cc`
- `hardening-observability` → `38f7d09`
- `hardening-backup` → `16848db`
- `hardening-httponly-cookie-auth` → `966fc56`
- `hardening-refresh-tokens` + `hardening-password-reset` → `e813dfa`

Remaining infra item (deferred, not blocking): `hardening-windows` — verify on Windows Docker Desktop + bare Linux. Needs a Windows host; deferred until needed.

### Track B — Product Development Phase 1 (DONE)
First batch of annotation-tool features shipped to `main` (backend + UI + COCO export):

| Todo | Commit(s) | Notes |
|---|---|---|
| `prod-polyline-tool` | `7906544` (api) + `4fab9b8` (web) | Open polyline geometry; key `L`; COCO export under `annotation.anvil.geometry_type` (Anvil extension). |
| `prod-rotated-bbox` | `7906544` + `4fab9b8` | OBB stored as `{cx,cy,w,h,angle_rad}`; key `O`; angle normalized to `[-π/2, π/2)`; exports 4 rotated corners + `annotation.anvil.obb`. |
| `prod-keypoint-tool` | `7906544` + `4fab9b8` | Per-class keypoint schema (names + edges). Key `K` drops in schema order; Cmd-click = occluded (v=1). COCO `keypoints` flat list + 1-based skeleton. Schema **immutable** once any annotation exists (409). |
| `prod-class-hierarchy` | `7906544` + `5621786` | `Class.parent_id`; same-project enforced; cycle prevention; depth ≤ 5; delete-blocks-children (409). UI: tree-indented Classes tab with parent picker (filters out descendants); COCO `category.supercategory` + `category.anvil.path`. |
| `prod-bulk-ops` | `7906544` + `5621786` | 4 endpoints all dataset-scoped, MAX_BULK=500, per-id failure list: bulk-delete, bulk-review (reset/reopen/reject), bulk-move (blocks `approved`), bulk-delete-by-class (skips locked images by default). UI: sticky action bar + multi-select with shift-range. |
| `prod-image-filters` | `7906544` + `5621786` | Server-side filters: `has_annotations`, repeatable `class_id`, sort variants (`oldest`/`recent`/`filename`/`most_labels`/`fewest_labels`/`uncertainty`), stable secondary sort on `Image.id`, new indexes. UI uses URL search params so filters survive refresh / shared links. |

Verification for this batch:
- `python3 -m ruff check apps/api/anvil_api` → All checks passed
- `cd apps/web && npx tsc --noEmit` → clean
- `ANVIL_ADMIN_EMAIL=admin@anvil.dev python3 scripts/prod_features_e2e.py` → ALL OK
  (covers hierarchy, cycle, kp-schema lock, polyline / OBB / keypoints validation + create, filters, sort, all 4 bulk endpoints, COCO export)

**Open product-dev todos remaining (in SQL `todos`, prefix `prod-*`):**
`prod-active-learning-queue`, `prod-api-tokens`, `prod-audit-log`, `prod-export-bundle-model`, `prod-export-yolo`, `prod-image-comments-mentions`, `prod-keyboard-help-modal`, `prod-onboarding-tour`, `prod-quality-dashboard`, `prod-storage-s3-backend`, `prod-task-assignment`, `prod-webhooks`, `prod-windows-verify`. See `docs/FUTURE.md` for directional backlog.

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
`5621786 feat(web): bulk-ops UI, server-side filters/sort, class hierarchy + keypoint schema editor`

## Starting a new session — paste this
> "Read `~/PersonalDev/anvil-cv/HANDOFF.md`. We are working on Track [A or B] only. Don't touch the other track's folders unless I explicitly ask. Then check `todos` table in SQL for current pending items in this track."
