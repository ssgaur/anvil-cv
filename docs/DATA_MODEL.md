# Canonical Data Model (training-ready)

Anvil stores annotations so they can be exported for **SAM2**, **SAM3**, and
generic detectors without re-annotating. This page explains the shape and, more
importantly, *why* each piece exists.

The guiding rule: **COCO is an export format, never the internal schema.** COCO
cannot express "a human confirmed this concept is absent", and that fact is
worth as much to a model as a mask.

## Layers

```
Project
 ├── Class      taxonomy entry: name, colour, geometry type   (SAM2 world)
 ├── Concept    canonical phrase: "red car", "leaf blight"    (SAM3 world)
 └── Dataset
      ├── Image
      │    ├── Query        (image x concept) + completeness flags
      │    └── Annotation   one instance: geometry + mask + provenance
      └── DatasetVersion    immutable snapshot for a training run
```

`Class` and `Concept` are deliberately separate. A Class is a fixed taxonomy
entry with a geometry type; a Concept is a *phrase you can ask a model about*.
`Concept.class_id` optionally bridges them, so a project can use SAM2 classes,
SAM3 concepts, or both without one forcing the other.

## Why `Query` exists

SAM3 does not learn from "image + object". It learns from:

> image + phrase → **every** matching instance, and whether that set is complete.

Those completeness flags cannot be reconstructed later, so they are stored at
annotation time:

| Field | Meaning |
|---|---|
| `is_exhaustive` | every instance of this concept in this image is annotated |
| `is_pixel_exhaustive` | the masks cover all target pixels |
| `status` | `unannotated` / `in_progress` / `verified_present` / `verified_absent` |

### The negative-example rule

`verified_absent` is the only way to record a trustworthy negative. An empty
instance list means "nobody looked yet" — exporting that as a negative would
teach the model that the concept is absent from images no one has reviewed.

The API enforces this both ways:

- marking `verified_absent` while instances exist → **409**
- marking `verified_present` with zero instances → **409**

## Provenance on every annotation

Needed to answer "how much human work did the AI actually save?", and to avoid
training a model on its own unreviewed output.

| Field | Purpose |
|---|---|
| `generated_by` | `human` / `sam2` / `sam3` / `imported` |
| `model_name`, `model_version_id`, `model_score` | which model, how confident |
| `human_verified` | a human explicitly accepted it |
| `human_corrected` | set automatically when a human edits a model mask |
| `original_geometry`, `original_mask_rle` | the model's untouched output |

Because the original is preserved, correction magnitude can be measured later
without having stored a separate audit log.

## Mask fidelity: `mask_rle`

Polygons are a **lossy** simplification — both SAM2 and SAM3 run
`approxPolyDP` before storage. That is fine for editing, poor as a training
target.

So each annotation may carry a compressed COCO RLE of the exact mask
alongside the polygon:

- the **polygon** drives the editor (draggable vertices)
- the **RLE** is the training target when present

Exporters should prefer `mask_rle` and fall back to the polygon.

## `DatasetVersion`: reproducible training

A model trained "on dataset X" is unreproducible the moment someone fixes a
mask. A version freezes the exact IDs:

```json
{
  "image_ids": [...], "annotation_ids": [...],
  "query_ids": [...], "concept_ids": [...],
  "seed": 42, "train_split": 0.8, "val_split": 0.1
}
```

Splits are assigned by hashing `seed:image_id`, not by shuffling, so an image
always lands in the same split for a given seed even as the dataset grows —
which keeps val/test honest over time.

Membership is **not** gated on `ImageStatus.READY`. READY means "SAM2 embedding
succeeded", an inference optimisation; SAM3 training needs no SAM2 embeddings,
and an image whose embedding failed still has valid pixels and annotations. The
human quality gate is `review_status` (default: approved only). The image-status
breakdown is reported in `stats.by_image_status` for transparency.

`stats` also records `by_generated_by`, `by_class`, `by_geometry_type`,
`annotations_with_mask_rle`, `images_without_annotations`, and the
present/absent/exhaustive query counts — enough to judge dataset health before
spending GPU hours.

## Mapping to trainers

| Target | Uses |
|---|---|
| **SAM2** | image + `geometry` (bbox/point prompt) + `mask_rle` as target |
| **SAM3** | image + `Query.text` + all instances + `is_exhaustive` + `verified_absent` negatives |
| **COCO** | `categories` from Class/Concept, `segmentation` from `mask_rle` or polygon |
| **YOLO** | bbox derived from geometry |

For SAM3 specifically, the upstream image loader derives query text from
**category names**, so the exporter emits `Concept.canonical_phrase` as the
category name. Aliases are stored but deliberately **not** injected into
training data — that would silently change the dataset (Phase 23).

## API

| Method | Path |
|---|---|
| GET/POST | `/api/projects/{id}/concepts` |
| PATCH/DELETE | `/api/concepts/{id}` |
| GET/POST | `/api/images/{id}/queries` |
| PATCH/DELETE | `/api/queries/{id}` |
| GET/POST | `/api/projects/{pid}/datasets/{did}/versions` |
| GET/DELETE | `/api/dataset-versions/{id}` |

`POST /api/images/{id}/queries` is idempotent per `(image, concept)` so
re-running a concept never forks its exhaustiveness metadata.

## Backward compatibility

Every new annotation column is nullable or defaulted, so existing SAM2 rows stay
valid with no backfill. Deleting a concept or query **detaches** instances
(`query_id = NULL`) rather than deleting them — the mask is still real work.

Legacy rows are backfilled by inference: `source='MODEL'` predates SAM3, so it
can only have been SAM2.

Covered by `scripts/concept_schema_e2e.py`.
