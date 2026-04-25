# API Reference

All endpoints are under `/api/` and require `Authorization: Bearer <jwt>` except `/auth/signup` and `/auth/login`. Live OpenAPI docs at <http://localhost:8000/docs> when the API is running.

## Auth

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| POST | `/auth/signup` | `{email, password}` | `{access_token}` |
| POST | `/auth/login` | `{email, password}` | `{access_token}` |
| GET | `/auth/me` | — | `UserOut` |

## Projects

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/projects` | list mine |
| POST | `/projects` | create |
| GET | `/projects/{id}` | detail |
| DELETE | `/projects/{id}` | |

## Datasets

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/projects/{p}/datasets` | list |
| POST | `/projects/{p}/datasets` | create |
| GET | `/datasets/{id}` | detail |
| DELETE | `/datasets/{id}` | |
| GET | `/datasets/{id}/export/coco` | streams a ZIP |
| POST | `/datasets/{id}/import/coco` | multipart ZIP upload |

## Images

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/datasets/{id}/images` | multipart upload (multi-file). Triggers worker for embedding pre-compute |
| GET | `/datasets/{id}/images` | list w/ filters: `?review_status=pending` |
| GET | `/images/{id}` | detail |
| PATCH | `/images/{id}/review` | `{status, reject_reason?}` |
| GET | `/files/{key:path}` | streams stored file (image/thumb) |

## Annotations

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/images/{id}/annotations` | list for image |
| POST | `/images/{id}/annotations` | create. Polygon / bbox / brush mask geometry in `data` |
| PATCH | `/annotations/{id}` | edit geometry, class, properties |
| DELETE | `/annotations/{id}` | |

## Classes & properties

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/projects/{p}/classes` | list |
| POST | `/projects/{p}/classes` | create with curated color |
| PATCH | `/classes/{id}` | rename / recolor |
| DELETE | `/classes/{id}` | |
| GET | `/classes/{id}/properties` | list |
| POST | `/classes/{id}/properties` | create — type ∈ `single` / `multi` / `text` |
| PATCH | `/properties/{id}` | |
| DELETE | `/properties/{id}` | |

## Comments (spatial pins)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/images/{id}/comments` | list |
| POST | `/images/{id}/comments` | `{x, y, text}` (image coords) |
| PATCH | `/comments/{id}` | edit text or resolved flag |
| DELETE | `/comments/{id}` | |

## ML inference & active learning

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/images/{id}/predict` | `{prompts: [{x, y, label}], box?}`. Uses active model version's adapter if set |
| GET | `/projects/{p}/datasets/{d}/next-uncertain` | returns next image ranked by entropy |

## Model versions

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/projects/{p}/model-versions` | list |
| GET | `/model-versions/{id}` | detail with full metrics |
| POST | `/projects/{p}/model-versions` | start training. Body: dataset slice + LoRA cfg |
| DELETE | `/model-versions/{id}` | refused while training; clears active; deletes adapter file |
| POST | `/projects/{p}/active-model` | `{version_id?}`. Null = base SAM2 |
| POST | `/projects/{p}/model-versions/compare` | `{a_id?, b_id?, n_samples?, dataset_id?, seed?}` → `CompareResult` |

`CompareResult` shape:

```json
{
 "a": {"version_id": "...", "per_class_iou": {"lime": 0.81, "cuke": 0.74}, "per_item": [{"image_id": "...", "iou": 0.78}, ...]},
 "b": {"version_id": null, "per_class_iou": {"lime": 0.62, "cuke": 0.55}, "per_item": [...]},
 "delta_per_class": {"lime": 0.19, "cuke": 0.19},
 "n_samples": 12
}
```

## ML service (internal, not for clients)

The API talks to the ML service at `:8001`. Documented for completeness:

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/predict` | image + prompts (+ optional adapter) → mask |
| POST | `/train` | items + cfg → trains LoRA, persists adapter, returns metrics |
| POST | `/evaluate` | items (+ optional adapter) → per-class + per-item IoU |

End users should not hit `:8001` directly. The API is the public surface.
