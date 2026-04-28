# Anvil Roadmap

Live status. Done = shipped to `main`. Order within a phase is suggested, not mandatory.

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

## Phase 4 — Polish & launch open
- [ ] **`finalize-name-domain`** — secure final name + domain (anvil.cv?)
- [ ] **`docs-site`** — Astro Starlight built from `docs/`
- [ ] **`public-demo`** — Hetzner + Replicate-hosted demo project
- [ ] **`launch-post`** — Show HN / Reddit / dev.to write-up
- [ ] **`launch`** — actually press post

## Out of scope (v0)
- Workflow Builder
- Polyline / Keypoint / Directional Vector / Instance ID tools
- Video annotation (multi-frame / masklets)
- Multi-org SaaS billing
- SSO / SAML
- Consensus / N-of-M annotation

## Future ideas (post-v0, ungroomed)
See [FUTURE.md](FUTURE.md).
