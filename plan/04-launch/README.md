# Phase 4 — Launch

## Tasks

### Docs site (Astro Starlight)
- [ ] Quickstart: `docker compose up` → first annotation in <5 min
- [ ] Self-hosting guide (Docker Compose, then Helm)
- [ ] Fine-tune walkthrough with cucumber dataset (the headline post)
- [ ] API reference (auto-gen from FastAPI OpenAPI)
- [ ] Architecture diagram + data model

### Public demo
- [ ] Hetzner CPX21 (~$10/mo): API + Postgres + Web + MinIO
- [ ] Replicate API for SAM2 inference (~$30/mo budget) — switch ML provider via env var
- [ ] Cloudflare DNS + auto TLS
- [ ] Demo project pre-seeded; readonly viewer mode for tourists
- [ ] "Try it now" button on docs homepage

### Distribution
- [ ] Single-binary `anvil-cv up` (PyInstaller? or just docker-compose)
- [ ] Helm chart in `charts/anvil`
- [ ] Homebrew formula

### Launch content
- [ ] Blog post: "We fine-tuned SAM2 on 47 cucumber leaves and beat V7 by 12 IoU" with charts + screenshots
- [ ] Show HN post (drafted, not posted until 7 days of self-use without crash)
- [ ] r/MachineLearning post (avoid r/computervision Mondays — saturated)
- [ ] Twitter/X thread with looping demo gif
- [ ] DM 5 university CV labs offering free white-glove onboarding

### Repo polish for stars
- [ ] Beautiful README with screenshots + GIF
- [ ] PolyForm Noncommercial 1.0.0 plus separate commercial licensing terms
- [ ] CODE_OF_CONDUCT, CONTRIBUTING, SECURITY
- [ ] Issue templates (bug / feature / question)
- [ ] GitHub Discussions enabled
- [ ] Make repo public (currently private)
- [ ] Add tags: `image-annotation`, `sam2`, `computer-vision`, `labeling`, `mlops`

## Acceptance
- Star count > 100 in first week post-launch (sanity floor; below = positioning is wrong)
- 3+ inbound emails from real teams asking about self-hosting
- 0 P0 bugs reported in first 72h (means we self-tested enough)
