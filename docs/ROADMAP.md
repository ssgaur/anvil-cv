# Anvil Roadmap

Live status. Done = shipped to `main`. Order within a phase is suggested, not mandatory.

For the next paid/team product track, see
[Paid Product Roadmap](PAID_PRODUCT_ROADMAP.md). That document is the canonical
plan for OSS vs Pro feature flags, review workflow management, Model Studio,
fine-tuning workflow stages, and the eight paid milestones.

## Phase 0 — Foundations
- [x] Repo scaffold (monorepo: web, api, ml, worker, storage)
- [x] Domain model + Postgres + SQLModel
- [x] CI (ruff, mypy, web typecheck)
- [x] **`docker-compose-dev`** — one-command install: `docker compose up -d --build` (Postgres + Redis + MinIO + 3 services + web + checkpoint auto-download)

## Phase 1 — Core annotation loop
- [x] JWT auth (signup / login)
- [x] Project / Dataset CRUD
- [x] Image upload + thumbnails + **embedding pre-compute on the worker**
- [x] Annotation canvas: Polygon, Bbox (8-handle resize + body drag), Brush (paint → marching-squares polygon)
- [x] Vertex editing on polygons
- [x] Click-to-segment via cached embeddings
- [x] **Multi-prompt SAM2** (positive/negative clicks + box prompt combined)
- [x] Per-image undo/redo (last 20)
- [x] Hold-Space pan, Fit / 1:1 controls, zoom % indicator
- [x] Keyboard shortcuts cheatsheet (`?` / `H`)
- [x] Class manager with curated WCAG-AA palette
- [x] Per-class properties: Single-select, Multi-select, Text
- [x] COCO instance-seg export
- [x] COCO instance-seg import (drag-drop ZIP → bootstrap dataset)
- [ ] **`annotation-canvas`** — small a11y / focus-ring / timer polish

## Phase 2 — The wedge
- [x] LoRA fine-tune of SAM2 mask decoder (runs on M1 CPU)
- [x] Model versions list with per-epoch metrics
- [x] **Per-class IoU** bars + val-IoU sparkline
- [x] **A/B compare** — side-by-side per-class table + per-image deltas
- [x] Delete model version (clears active, removes adapter file)
- [x] Promote-to-active (auto-annotate uses the active version)
- [x] Active-learning **Next uncertain** queue (entropy-ranked)

## Phase 3 — Team & quality partial
- [x] Review flow: Approve / Reject / Needs-changes
- [x] Predefined reject reasons
- [x] **Spatial pin comments** anchored to canvas coords
- [x] Members + roles (RBAC: admin/reviewer/labeler/viewer) — `9e95b47`
- [x] First-run /setup wizard — `3559026`
- [x] Real auth: JWT + GitHub OAuth — `176197c`
- [x] HttpOnly cookie sessions + CSRF + token-version invalidation — `966fc56`
- [x] Rotating refresh tokens (family revocation, reuse detection) + password reset — `e813dfa`
- [x] Rate limiting (login/signup/oauth/reset) — `6a01137`
- [x] Observability: /metrics, /ready, JSON logs — `38f7d09`
- [x] Pagination + virtualized image list — `6e790cc`
- [x] V7-parity keyboard shortcuts — `2920fa4`
- [x] Toast UX (replaces blocking alerts) — `3971a49`
- [x] `anvil-cli` backup + restore — `16848db`
- [ ] **`quality-dashboard`** — per-annotator throughput + IoU agreement matrix

## Phase 4 — Foundation Model Expansion (SAM3)
- [x] Provider abstraction (`SAM2Provider`, `SAM3Provider`) with memory-budget LRU eviction (`ANVIL_MODEL_MEMORY_BUDGET_GB`)
- [x] SAM3 open-vocabulary concept segmentation (`POST /api/images/{id}/predict-concept`) + tunable threshold
- [x] SAM3 interactive point/box segmentation (`POST /api/images/{id}/predict` with `base_model="sam3"`)
- [x] Workview `Model: base SAM3` selector + floating SAM3 concept bar (`Find all ✨`)
- [x] Host-cached Hugging Face setup (`make sam3-setup`)

## Phase 5 — Paid / Pro product track
- [ ] **`paid-feature-flag-foundation`** — Edition flags, API/UI guards, permission vocabulary
- [ ] **`paid-project-memberships`** — Org/project memberships, multiple roles per user, team admin UI
- [ ] **`paid-review-queue-mvp`** — WorkItem/Assignment, My Queue, assign/reassign, workload counts
- [ ] **`paid-workflow-engine`** — Workflow/stage/edge schema, accepted/rejected routing, default workflow
- [ ] **`paid-workflow-builder-ui`** — Workflow canvas, stage palette, node config, item counts
- [ ] **`paid-advanced-review`** — Second review, consensus, rejection issue threads, audit/analytics
- [ ] **`paid-finetuning-studio-mvp`** — Training snapshots, approved-label dataset builder, eval/deploy/rollback
- [ ] **`paid-defect-finetuning-ai-review`** — Crop sampling, hard negatives, small-object metrics, AI review, active learning

## Future Backlog
See [FUTURE.md](FUTURE.md).
