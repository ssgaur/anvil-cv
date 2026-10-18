# Features

Comprehensive list of what's in `main`. Each feature has a tiny "How to use" and the relevant keyboard shortcut where applicable.

## Workview (annotation canvas)

The single page where 90% of user time is spent: `WorkviewPage.tsx`.

### Tools

| Tool | Shortcut | What it does |
| --- | --- | --- |
| **Pan** | `V` or hold `Space` | Pan across the canvas |
| **Auto-Annotate (SAM2 / SAM3)** | `A` | Click = positive point, Shift-click = add positive point, Alt-click = negative point, drag = box prompt. When **`Model: base SAM3`** is selected in the top bar, also exposes the **SAM3 concept bar** (`Find all ✨` + confidence threshold slider) for open-vocabulary multi-instance segmentation |
| **Polygon** | `P` | Click to add vertices, `Enter` to close and save (≥ 3 points) |
| **Polyline** | `L` | Click to add vertices, `Enter` to save open path (≥ 2 points) |
| **Bounding Box** | `B` | Drag to draw. **8-handle resize** (4 corners + 4 edges) + body drag to move |
| **Rotated Box (OBB)** | `O` | Drag to place, then drag the orange rotation handle or corner handles |
| **Brush** | `R` | Paint a mask (`Shift` to erase, `[` / `]` to resize, `Enter` to extract polygon) |
| **Keypoints** | `K` | Drop keypoints in schema order (`Cmd/Ctrl-click` for occluded `v=1`) |
| **Comment Pin** | `T` | Click on the image to anchor a spatial review note at `(x, y)` |
| **Undo / Redo** | `⌘Z` / `⇧⌘Z` | Per-image history stack (up to 20 operations) |
| **Fit / 1:1** | `F` / `0` | Fit image to viewport or reset to 100% zoom |
| **Cheatsheet** | `?` or `H` | Toggle keyboard shortcuts & tips modal |

### Interaction polish

- Active class color follows the vertex/edge of the in-progress shape
- Hover an existing annotation → it highlights and shows class name
- Right-click on a polygon vertex → delete vertex
- Hold-`Space` cursor changes to grab/grabbing
- Status bar shows current zoom %, mouse coords, selected tool

## SAM2 fine-tuning (the wedge)

`apps/ml/anvil_ml/training.py` + `apps/ml/anvil_ml/lora.py`

### How to use

1. Project → **Models** tab → **Train new version**
2. Pick the dataset slice (default: all approved annotations)
3. Optionally filter by class
4. Click **Start training**
5. Watch per-epoch loss + val IoU stream into the row
6. When done, **Set active** → all subsequent click-to-segment calls use the adapter

Default config: rank 8, alpha 16, lr 1e-4, epochs 20, device CPU. Trainable params ≈ 47K.

Deep-dive in [FINE_TUNING.md](FINE_TUNING.md).

## Model versions + A/B compare

`ProjectDetailPage.tsx::ModelsTab`

- **Version list** — each row shows status, mean IoU, **per-class IoU bars** (color-thresholded green/yellow/red), val-IoU **sparkline** across epochs, "Δ vs base" callout, **Set active**, **Delete**
- **Compare panel** — pick A and B; runs both adapters on N (default 12) approved annotations
 - Side-by-side per-class IoU table with deltas
 - Collapsible per-image breakdown
 - Either side can be null → base SAM2 (lets you sanity-check "did fine-tune actually help?")

## Active learning

The "Next uncertain image" button on `DatasetDetailPage.tsx` ranks unlabeled images by the active model's mask-score entropy and serves the most-uncertain one first. Endpoint: `GET /api/projects/{p}/datasets/{d}/next-uncertain`.

## Class manager + class properties

`ProjectDetailPage.tsx::ClassesTab`

- **Curated palette** — 24 WCAG-AA-contrast-checked colors. No clashing reds.
- **Per-class properties** (`apps/api/anvil_api/models.py::ClassProperty`):
 - **Single-select** — radio (e.g., damage severity: minor/major/critical)
 - **Multi-select** — checkboxes (e.g., visible features: stem/leaf/blemish)
 - **Text** — freeform note
- Annotation property values stored in `Annotation.properties` JSONB keyed by property id

## Review flow

`apps/api/anvil_api/routes/images.py::patch_review` + `WorkviewPage.tsx`

- Per-image review status: `pending` / `approved` / `rejected` / `needs_changes`
- **Predefined reject reasons** (configurable per project): "Mask too tight", "Wrong class", "Missing instance", etc.
- **Spatial pin comments** (`apps/api/anvil_api/routes/comments.py`) — click anywhere on the canvas to drop a pin, write a comment. Comments anchored to image coords; survive zoom/pan.
- Reviewer view filters: pending / mine / rejected

## COCO import & export

- **Export:** `GET /api/datasets/{id}/export/coco` → ZIP with `instances.json` + `images/`
- **Import:** drag-drop a COCO ZIP onto the dataset → bootstraps classes + annotations
- Supports polygon segmentation; bbox-only annotations imported as bbox

## Project / dataset / image management

- Multi-dataset projects
- Image upload (multi-file, with progress)
- Embedding pre-compute kicked off automatically on the worker (Arq)
- Thumbnails generated on upload

## Auth

- JWT (HS256), 7-day default expiry
- Email + password signup/login
- `Authorization: Bearer <token>` on all `/api/` routes except `/auth/*`

## Coverage matrix

| Feature | Backend | Frontend | E2E test |
| --- | --- | --- | --- |
| Auth | yes | yes | `e2e_smoketest.py` |
| Project / Dataset / Image | yes | yes | `e2e_smoketest.py` |
| Polygon / Bbox / Brush / Click | yes | yes | `e2e_smoketest.py`, `bbox_resize_e2e.py`, `undo_redo_e2e.py` |
| SAM2 predict | yes | yes | `sam2_smoketest.py` |
| LoRA fine-tune | yes | yes | `finetune_e2e.py` |
| Model versions + A/B | yes | yes | `model_versions_e2e.py` |
| Review flow + comments | yes | yes | `review_flow_e2e.py` |
| Class properties | yes | yes | `class_properties_e2e.py` |
| COCO export + import | yes | yes | `coco_roundtrip_e2e.py` |
| Quality dashboard | no | no | planned |
