# Anvil — Latest Plan

> Open-source image-annotation platform with built-in SAM2 fine-tuning.
> One-line wedge: **"V7 Darwin without the $500/seat fee, plus a Fine-Tune button."**

This file is the live source of truth. Each phase folder contains feature briefs.

---

## Where we are right now

**Phase 0 (Foundations) — DONE ✅**
- Repo `ssgaur/anvil-cv` scaffolded (apps/web, apps/api, apps/ml, packages/storage, scripts/)
- CI lint + typecheck wired
- `.env`-driven config; absolute paths for SAM2 + storage
- Postgres in Rancher Docker
- Python 3.11 venv with all deps
- SAM2 v2.1 Hiera-B+ checkpoint downloaded (309MB)

**Phase 1 (Core annotation loop) — BACKEND + UI DONE, FE2E PASSING ✅**
- Backend: auth (JWT + Argon2), Project/Dataset/Image/Class/Annotation CRUD, file storage abstraction, ML client, /predict
- ML service: SAM2 on **MPS** (~3s embed, <1s predict from cached embedding)
- Frontend: Login, Projects list, Project detail (Data/Classes tabs), Dataset detail (drag-drop upload, status filters, polling), **Workview canvas (Konva)**: pan/zoom, auto-annotate (click-to-segment), polygon, bbox, keyboard shortcuts, thumbnail strip, per-image timer
- E2E: Playwright test (`apps/web/e2e/anvil.spec.ts`) passes — drives the full flow in real Chromium

**What's left in Phase 1 (next sprint):**
- COCO export
- Per-class annotation property values (single/multi/text)
- Brush tool → polygon conversion
- Polygon vertex editing (drag, insert, delete)
- Bbox edit handles
- Bulk delete / bulk send-to-review

---

## How to run locally

```bash
# 1. Postgres
docker start anvil-postgres

# 2. ML service
cd ~/PersonalDev/anvil-cv && source .venv/bin/activate && set -a && source .env && set +a
cd apps/ml && PYTHONPATH=$PWD nohup python -m uvicorn anvil_ml.main:app --host 127.0.0.1 --port 8001 </dev/null > ../../logs/ml.log 2>&1 & disown

# 3. API
cd ~/PersonalDev/anvil-cv/apps/api && PYTHONPATH=$PWD nohup python -m uvicorn anvil_api.main:app --host 127.0.0.1 --port 8000 </dev/null > ../../logs/api.log 2>&1 & disown

# 4. Web
cd ~/PersonalDev/anvil-cv/apps/web && nohup pnpm dev --host 127.0.0.1 </dev/null > ../../logs/web.log 2>&1 & disown
open http://127.0.0.1:5173

# 5. Smoke tests
python ~/PersonalDev/anvil-cv/scripts/e2e_smoketest.py            # backend only
cd ~/PersonalDev/anvil-cv/apps/web && pnpm exec playwright test    # full E2E
```

Default admin: `admin@anvil.dev` / `admin`

---

## Roadmap

- `00-foundations/` — done
- `01-core-annotation-loop/` — most done; remaining tasks listed there
- `02-the-wedge/` — fine-tuning, model versions, active learning (the differentiator)
- `03-team-quality/` — review flow, members, quality dashboard
- `04-launch/` — docs site, demo deploy, HN post

Each folder has a `README.md` with feature briefs.

---

## Open product decisions

1. **Brand name for npm/pypi/domain.** `anvil` is taken on PyPI (anvil.works). Need final pick before launch.
2. **First public-demo dataset.** Cucumber? COCO mini? Pothole detection?
3. **Replicate vs Modal vs self-hosted GPU** for the public demo predict endpoint.
4. **Launch headline.** Currently leaning on the "we beat V7 on 47 cucumber leaves" angle.

---

## Quality gates

Before any feature ships:
1. Backend smoke test (`scripts/e2e_smoketest.py`) green.
2. Playwright E2E green.
3. Manual click-through of Workview on M1 still snappy (<1s click-to-polygon after embed).
4. `pnpm typecheck` and API import-time `python -c "from anvil_api.main import app"` clean.
