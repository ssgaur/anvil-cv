# Architecture

## Services

```
┌──────────────┐ REST ┌──────────────┐ HTTP ┌──────────────┐
│ web :5173 │ ──────────► │ api :8000 │ ──────────►│ ml :8001 │
│ React+Konva │ │ FastAPI+SQL │ │ FastAPI+SAM2 │
└──────────────┘ └──────┬───────┘ └──────┬───────┘
 │ │
 ┌─────────┴─────────┐ reads cached
 ▼ ▼ embeddings .npz
 ┌──────────┐ ┌──────────┐
 │ Postgres │ │ Storage │◄─── worker writes
 └──────────┘ │ local/S3 │ embeddings on
 └──────────┘ image upload
 ▲
 ┌──────┴──────┐
 │ worker (Arq)│
 │ + Redis │
 └─────────────┘
```

| Service | Port | Tech | Responsibility |
| --- | --- | --- | --- |
| `web` | 5173 | Vite + React + TS + Konva + Zustand | Workview canvas, project/dataset UI, model-versions UI |
| `api` | 8000 | FastAPI + SQLModel + Postgres | REST API, auth, persistence, calls `ml` for predict/train/eval |
| `ml` | 8001 | FastAPI + SAM2 + PyTorch | Predict masks, train LoRA adapters, evaluate IoU |
| `worker` | — | Arq + Redis | Pre-compute SAM2 image embeddings on upload |
| storage | — | local / S3 / MinIO / Azure / GCS | Uploaded images, embeddings, adapter `.pt` files |

## The single most important design decision: **encoder cache**

SAM2's image encoder (Hiera-B+, ~80M params, ViT) is the expensive part. Anvil precomputes encoder output once at upload time and **never runs the encoder again** for that image — not for predict, not for training.

```
Image upload
 │
 ▼
worker pulls from Redis queue
 │
 ▼
ml /predict (or worker calls predictor.set_image once)
 │
 ▼
embeddings/<image_id>.npz ◄──── stored alongside the image
```

Then:

- **Click-to-segment:** `predictor._restore_features(npz)` → prompt encoder + mask decoder. Sub-second on M1.
- **Training:** same restore, just iterate. The encoder forward is never in the training loop, so backward is tiny.

This is what makes M1 LoRA fine-tuning possible. See [FINE_TUNING.md](FINE_TUNING.md).

## Request flow: click-to-segment

```
user clicks on canvas
 ─► web: POST /api/images/{id}/predict { prompts: [{x,y,label:1}] }
 ─► api validates, looks up image + active model version
 ─► ml-client: POST ml:8001/predict { image_path, embedding_path, prompts, adapter_path? }
 ─► ml: load embeddings → set on predictor → run prompt enc + mask dec → return RLE mask
 ◄─── PredictOut { mask, score, ... }
 ◄─── api returns to web
 ◄─── web rasterizes mask → polygon → renders on canvas
```

Latency on M1, after first warm-up: **~80–200 ms** end-to-end.

## Request flow: fine-tune

```
user clicks "Fine-tune" with class filter + dataset slice
 ─► api creates ModelVersion(status=training)
 ─► spawns _train_async background task
 ─► gathers annotations + class metadata, builds payload
 ─► ml-client: POST ml:8001/train { items: [{image_path, embedding_path, mask, class_id, ...}], cfg }
 ─► ml: train_lora() — inject LoRA, freeze encoder/prompt-enc, train decoder
 per epoch: _evaluate() → {mean_iou, per_class_iou, per_item}
 ─── persists adapter to storage as <version_id>.pt
 ◄─── final metrics
 ─► api updates ModelVersion(status=ready, metrics={...val_iou_per_class})
 ◄─── web polls list, renders per-class IoU bars + sparkline
```

## Request flow: A/B compare

```
user picks A and B versions in ComparePanel
 ─► POST /api/projects/{id}/model-versions/compare { a_id, b_id, n_samples=12 }
 ─► api samples N annotations from project's review-approved set
 ─► ml-client: POST ml:8001/evaluate twice (once with adapter A, once with B)
 ◄─── per_class_iou + per_item for each side
 ─► api computes deltas, returns CompareResult
 ◄─── web renders side-by-side table + per-image breakdown (collapsible)
```

`null` on either side means base SAM2 (no adapter).

## Data model (high level)

```
User ──┐
 ├── Project ──┬── Dataset ──┬── Image ──┬── Annotation ── ClassProperty values (JSONB)
 │ │ │ ├── Comment (spatial pin)
 │ │ │ └── review_status, reject_reason
 │ ├── Class ────┴── ClassProperty (single|multi|text)
 │ └── ModelVersion ── metrics (JSONB: mean_iou, per_class_iou, val_iou_per_class[])
 └── (auth)
```

All metric / property / config blobs are **JSONB**. We don't bend the schema for every new feature.

## Storage layout

```
<storage_root>/
├── images/<image_id>.<ext> original upload
├── thumbs/<image_id>.jpg 256×256 thumbnail
├── embeddings/<image_id>.npz cached SAM2 image features
└── adapters/<version_id>.pt LoRA adapter (~200 KB each)
```

Storage backend is pluggable via `packages/storage/`. Default is `local` (the `data/` dir).

## Why three processes (not one)

- **Hot reload separation.** Editing the React app doesn't restart the SAM2 model load (~3 s).
- **Crash isolation.** ML segfaults from PyTorch don't take down the API.
- **Scale path.** In production, ML can run on a GPU box, API on a small CPU box. Same code.
