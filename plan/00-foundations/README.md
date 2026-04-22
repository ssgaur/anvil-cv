# Phase 0 — Foundations (DONE)

Status: ✅ All shipped.

## Completed
- [x] Monorepo scaffold (`apps/web`, `apps/api`, `apps/ml`, `packages/storage`, `scripts/`)
- [x] GitHub repo `ssgaur/anvil-cv` (initial commits pushed)
- [x] CI: GitHub Actions for lint + typecheck on PRs
- [x] Python 3.11 venv via `uv`; all deps locked
- [x] Postgres 16 via Rancher Docker (`anvil-postgres` container)
- [x] SAM2 cloned to `vendor/sam2/`, pip-installed editable
- [x] `sam2.1_hiera_base_plus.pt` downloaded (309MB, in `.gitignore`)
- [x] Single `.env` with absolute paths shared by API + ML
- [x] `pydantic-settings` config with `extra="ignore"` so each service ignores foreign keys
- [x] SAM2 verified on M1 MPS (`scripts/sam2_smoketest.py`): embed 3.7s, predict <1s, IoU score 0.977

## Notable bug-fixes from this phase (avoid re-tripping)
1. `from __future__ import annotations` BREAKS SQLModel relationships — never use in `models.py`.
2. SQLModel `session.exec(select(Model.col))` returns scalars, not 1-tuples.
3. `EmailStr` rejects `.local`/`.test` TLDs — use `.dev` for default seed admin.
4. `httpx.AsyncClient` cannot be reused across `asyncio.run()` loops — make ML client stateless.
5. SAM2 `_orig_hw` must be `List[Tuple[int, int]]` — `tolist()` gives `List[List[int]]`, must `tuple(row)` each.
