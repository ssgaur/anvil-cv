# Anvil Roadmap

## Phase 0 — Foundations
- [x] Repo scaffold (this commit)
- [ ] Docker compose dev stack (Postgres + Redis + MinIO + API + ML + Worker + Web)
- [ ] CI: lint, typecheck, tests, docker build
- [ ] Domain model + Alembic migrations

## Phase 1 — Core annotation loop
- [ ] Auth (email+password + GitHub OAuth)
- [ ] Org / Project / Dataset CRUD
- [ ] Image upload + thumbnail generation
- [ ] **Embedding pre-compute on upload** (perf wedge vs V7)
- [ ] Annotation canvas: Polygon, Bbox, Brush + zoom/pan + vertex edit
- [ ] Class manager with curated WCAG-AA color palette
- [ ] Auto-annotate via cached embeddings (single unified mode)
- [ ] COCO / YOLO / Pascal VOC / mask PNG export

## Phase 2 — The wedge
- [ ] Fine-tune UI: dataset slice picker + LoRA config + live loss/IoU chart
- [ ] Model versions list + per-class eval IoU + A/B compare
- [ ] Promote-to-default auto-annotate
- [ ] Active-learning "Next" button (uncertainty-ranked)

## Phase 3 — Team & quality
- [ ] Review flow + predefined reject reasons + spatial comments
- [ ] Quality dashboard (per-annotator sparklines, IoU agreement matrix)
- [ ] Members + roles
- [ ] In-UI COCO zip drag-drop import

## Phase 4 — Polish & launch
- [ ] HuggingFace datasets export
- [ ] Docs site (Astro Starlight)
- [ ] Public demo (Hetzner + Replicate)
- [ ] Show HN / Reddit launch

## Out of scope (v0)
- Workflow Builder
- Polyline / Keypoint / Directional Vector / Instance ID
- Video annotation
- Multi-org SaaS billing
- SSO / SAML
- Consensus annotation
