# Phase 2 — The Wedge (Fine-Tune + Active Learning)

This is what V7 doesn't have. Without this, Anvil is "another CVAT." With this, Anvil is the only OSS tool where labelers see their model improve on screen.

## Goal
A vision team labels 50 cucumbers, hits **Fine-Tune**, sees a live loss/IoU chart, promotes the new adapter to default auto-annotate, and the next batch's clicks return *their* domain mask, not generic SAM2.

## Tasks

### Fine-tune backend
- [ ] `ModelVersion` table (id, project_id, base, lora_path, training_dataset_snapshot_json, eval_metrics_json, status, created_by, created_at)
- [ ] `TrainingRun` table (id, model_version_id, config_json, logs_uri, started_at, ended_at, status)
- [ ] `POST /api/projects/:id/training-runs` — body: dataset_id slice + LoRA config
- [ ] Worker (arq + redis): runs SAM2 LoRA fine-tune script
- [ ] Live progress via Server-Sent Events from `/api/training-runs/:id/stream`
- [ ] Auto eval on held-out 20% slice; persist per-class IoU

### Fine-tune script (Python)
- [ ] LoRA on mask decoder only (rank 8, lr 1e-4, 50 epochs default)
- [ ] Pull annotated images from storage; convert in-DB polygons → binary masks
- [ ] Save adapter as `.safetensors` to storage; register in DB
- [ ] Configurable: rank, lr, epochs, augmentations

### Model versions UI
- [ ] `ProjectDetailPage` "Models" tab: list versions, status, eval IoU per class
- [ ] **Train New** modal: dataset slice picker, LoRA config form, kick off
- [ ] Live training chart (loss + val IoU)
- [ ] **Promote to default** button — sets `Project.default_model_version_id`
- [ ] A/B compare: pick 2 versions, run on same image, side-by-side overlays

### Auto-annotate uses active model
- [ ] ML service `/predict` accepts `model_version_id` (defaults to project's default)
- [ ] Adapter loaded lazily, cached per version
- [ ] Annotation row records `model_version_id` for traceability

### Active learning
- [ ] Compute uncertainty on every Image after embedding (use SAM2 mask-confidence head + entropy across multimask outputs)
- [ ] `Image.uncertainty_score` column (already in model)
- [ ] **"Next" button** in Workview top bar: jumps to highest-uncertainty unlabeled image in dataset
- [ ] Sort filter on Dataset page: "Hardest first"

## Acceptance
- Fine-tune 50 cucumber images on M1 (CPU/MPS) in <10 min
- Promoted adapter changes auto-annotate output measurably (visual diff on a held-out image)
- Active-learning "Next" surfaces visibly harder images first
