# Copilot Handoff - Anvil

This file is the current high-signal handoff for fresh Copilot CLI sessions.
Read it after `.github/copilot-instructions.md` and before touching code.

## Current focus

We are on **Track B - Product / self-hosted app**, now moving from public launch
polish into
**paid product development**:

1. Full review management workflow for teams.
2. Full fine-tuning workflow for domains where base SAM2 fails, such as fruit
   defects.
3. Feature flags/edition split so the public noncommercial edition stays clean
   and paid features are gated.

Canonical plan: [`docs/PAID_PRODUCT_ROADMAP.md`](../docs/PAID_PRODUCT_ROADMAP.md).

## User working style

- For small bug-fix mode, the user recently asked to push directly to
  `main/master` instead of creating PRs.
- For large product work, the user wants planning first, then implementation.
- The user tests locally by reloading <http://localhost:5173>.
- After web changes, rebuild local Docker web with:

```bash
docker compose up -d --build web
curl -fsS http://localhost:5173/
```

## Current known-good state

- Repo: `/Users/shailendrasingh/PersonalDev/anvil-cv`
- Branch at last handoff: `main`
- Latest known pushed commit: `cbdfa2d fix(web): replace remaining browser confirms with app dialogs`
- Local web was responding at <http://localhost:5173/>
- No known uncommitted code changes after popup cleanup before this handoff.

## What has shipped recently

### Hardening/auth

- RBAC roles: `admin`, `reviewer`, `labeler`, `viewer`.
- GitHub/Google-capable auth groundwork, HttpOnly cookie sessions, refresh-token
  rotation, CSRF, password reset, rate limiting.
- First-run setup wizard, backup/restore, `/ready`, `/metrics`, JSON logs.

### Annotation product features

- Polyline tool.
- Rotated bounding box (OBB) tool with edit/rotate handles.
- Keypoint tool.
- Class hierarchy.
- Bulk dataset operations.
- Server-side filters/sort.
- COCO import/export extensions.
- Comments and review flow.
- Model versions, active model, compare, and LoRA fine-tuning foundation.

### Workview/UI bug polish

- Brush cursor is now visible with a class-colored footprint preview.
- Selecting an annotation exits active drawing tools and enters edit mode.
- Tool helper panels hide during annotation editing.
- Polygon/polyline confusion fixed by making polygon preview close/fill after
  three points.
- OBB creation immediately selects the new box and shows rotate handle.
- OBB helper panel uses neutral styling like other tools.
- Native browser `confirm()` popups were replaced with in-app `ConfirmDialog`
  across admin, dataset, project, model, class, and Workview comment deletes.

## Current review foundation

Existing simple review model:

- `Image.review_status`: `new`, `in_progress`, `submitted`, `approved`,
  `rejected`.
- Review metadata: `submitted_at`, `submitted_by`, `reviewed_at`,
  `reviewed_by`, `review_reason`.
- Review route: `PATCH /api/images/{image_id}/review`.
- Transitions:
  - submit: `new|in_progress|rejected -> submitted`
  - approve: `submitted -> approved`
  - reject: `submitted -> rejected`
  - reopen: `approved|rejected -> in_progress`
- Approve/reject requires reviewer/admin and blocks self-review.
- Reject requires a reason and creates a `[Reject]` comment.

Paid review should not keep stretching `Image.review_status`. Add
`Workflow`, `WorkflowStage`, `WorkflowEdge`, `WorkItem`, `Assignment`,
`ReviewRound`, `ReviewDecision`, and `AuditEvent` abstractions.

## Current role foundation

Current RBAC is a single global `User.role` with hierarchy in
`apps/api/anvil_api/auth.py`. Paid should keep that as the public-edition
fallback and add:

- org/project memberships,
- multiple roles per user,
- permission checks such as `project.annotation.review` and
  `project.model.deploy`,
- workflow-stage assignment policy,
- self-review prevention at workflow level.

## Current fine-tuning foundation

Existing model flow:

- `ModelVersion` stores status, progress, metrics, adapter key, dataset, config.
- `routes/models.py` supports list/get/delete/create, set active, compare.
- Training is currently LoRA on SAM2 mask decoder, using cached embeddings.
- `apps/ml/anvil_ml/training.py` currently rasterizes polygon and bbox masks.
- Active model affects `/predict` adapter selection.

Paid fine-tuning should become **Model Studio**:

- training dataset snapshots,
- approved-label-only dataset builder,
- training run queue/logs,
- per-class and small-object metrics,
- visual failure gallery,
- deploy/rollback model versions,
- workflow-stage model deployment,
- AI review and active-learning loop.

## Important files

| File | Why it matters |
| --- | --- |
| `docs/PAID_PRODUCT_ROADMAP.md` | Paid/Pro product plan and milestone order |
| `apps/api/anvil_api/models.py` | SQLModel source of truth; do not add future annotations here |
| `apps/api/anvil_api/auth.py` | Current global RBAC; paid permissions will extend this |
| `apps/api/anvil_api/routes/images.py` | Review transitions and `/predict` |
| `apps/api/anvil_api/routes/models.py` | Model version and training APIs |
| `apps/ml/anvil_ml/training.py` | SAM2 LoRA training/evaluation |
| `apps/web/src/pages/WorkviewPage.tsx` | Annotation canvas, tools, review UI |
| `apps/web/src/pages/DatasetDetailPage.tsx` | Dataset filters, bulk actions, export |
| `apps/web/src/pages/ProjectDetailPage.tsx` | Classes and Models tabs |
| `apps/web/src/components/ConfirmDialog.tsx` | Reusable in-app confirmation modal |
| `scripts/review_flow_e2e.py` | Current review regression |
| `scripts/model_versions_e2e.py` | Current model/fine-tuning regression |

## Current pending product backlog

SQL `todos` had 11 pending product items before the paid roadmap was added:

- `prod-active-learning-queue`
- `prod-api-tokens`
- `prod-audit-log`
- `prod-export-bundle-model`
- `prod-export-yolo`
- `prod-image-comments-mentions`
- `prod-quality-dashboard`
- `prod-storage-s3-backend`
- `prod-task-assignment`
- `prod-webhooks`
- `prod-windows-verify`

The paid roadmap should be implemented as larger milestone todos, with those
existing backlog items folded in where appropriate.

## Recommended next implementation order

1. Paid foundation: feature flags, edition-aware API/UI guards, permission
   vocabulary, audit event table.
2. Project memberships: org/project membership tables, multi-role users,
   admin/team UI.
3. Review Queue MVP: `WorkItem`, `Assignment`, My Queue, assign/reassign, queue
   filters.
4. Workflow engine: workflow/stage/edge schema, Dataset/Annotate/Review/Complete
   stages, accepted/rejected routing.
5. Workflow Builder UI: V7-inspired canvas, stage palette, config panel, counts.
6. Advanced review: second review, consensus, issue threads, analytics.
7. Fine-tuning Studio MVP: snapshots, approved-label-only training sets, run
   queue/logs, compare/deploy/rollback.
8. Defect fine-tuning and AI review: crops, hard negatives, class imbalance,
   failure gallery, AI review, active learning.

## Validation commands

Use only checks that already exist:

```bash
ruff check .
cd apps/web && npx tsc --noEmit
cd apps/web && npm run build
python3 scripts/review_flow_e2e.py
python3 scripts/model_versions_e2e.py
mkdocs build --strict
```

For docs-only changes, `mkdocs build --strict` is enough.

## Critical gotchas

- Never add `from __future__ import annotations` to
  `apps/api/anvil_api/models.py`; it breaks SQLModel relationship resolution.
- React/Konva TS needs explicit `type` imports for `FormEvent`, `ReactNode`,
  etc.
- `load_lora()` already calls `inject_lora()`; do not double-wrap adapters.
- Postgres-only SQL is used (`JSONB`, `ALTER TABLE ... IF NOT EXISTS`).
- Vite dev needs stdin redirected when launched with `nohup`.
- No `pkill`/`killall`; kill literal PIDs only.
- Do not reintroduce native browser `confirm`, `alert`, or `prompt` popups in
  the web app. Use `ConfirmDialog` or toast patterns.
