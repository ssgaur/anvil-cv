# Phase 1 — Core Annotation Loop

Status: 🟢 Backend + Frontend MVP shipped. Remaining tasks below.

## Goal
Anyone can sign up, create a project, upload images, draw polygons / bboxes / SAM2-auto-annotations, and export.

## Done
- [x] Auth (signup, login, /me, JWT)
- [x] Domain models (User, Project, Dataset, Image, Class, Annotation)
- [x] Local storage provider (S3/Azure/GCS pluggable later via abstract interface)
- [x] Image upload + thumbnail generation
- [x] Background SAM2 embedding precompute on upload
- [x] `/api/images/:id/predict` (point + box prompts) using cached embedding
- [x] Class manager with curated 20-color WCAG palette
- [x] Annotations CRUD
- [x] **Frontend**: Login, Projects, Project (Data + Classes tabs), Dataset (grid, upload, polling), Workview (Konva canvas, pan/zoom, auto/poly/bbox tools, keyboard shortcuts, thumbnail strip, timer)
- [x] Playwright E2E test passing

## Remaining (next sprint, P1.5)

### Annotation tools
- [ ] **Polygon vertex editing**: drag existing vertices, double-click to add, right-click to delete
- [ ] **Bbox resize handles**: 8-handle resize on selection
- [ ] **Brush tool**: paint mask → marching squares to polygon on save
- [ ] **Multi-point auto-annotate**: shift-click adds positive, alt-click adds negative
- [ ] **Box-prompt auto-annotate**: drag a box, SAM2 returns mask within
- [ ] **Undo/redo** stack (Ctrl+Z) — at least last 20 ops per image

### Class metadata
- [ ] `ClassProperty` model (name, kind=single|multi|text, options)
- [ ] On-canvas property picker when annotation is created
- [ ] Backend: store `property_values` JSON on Annotation

### Export
- [ ] **COCO** (instance segmentation) — biggest user-ask
- [ ] **YOLO** (txt per image)
- [ ] **Pascal VOC** XML
- [ ] **Mask PNGs** (one per class) zip
- [ ] **HuggingFace `datasets`** parquet
- [ ] Background export job + signed download URL

### Dataset workflow
- [ ] Bulk select + delete + send-to-review
- [ ] "Send to Review" status transition (currently no review yet — Phase 3)
- [ ] Per-image notes / comments

### Polish
- [ ] Drag-drop COCO import (V7 forces CLI; we win here)
- [ ] Resumable uploads (>500 image batches)
- [ ] Image search by filename / class / annotator
- [ ] "Nothing to label" empty-state next to active-learning Next button (Phase 2)

## Acceptance
- Run `pnpm exec playwright test` and the full happy-path passes in <15s
- Manual: upload 50 cucumber images, auto-annotate 10, export COCO, train SAM2 fine-tune externally with the COCO and verify it loads
