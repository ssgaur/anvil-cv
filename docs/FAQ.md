# FAQ

### What is Anvil?

Anvil is a self-hosted, source-available computer vision annotation platform that integrates **SAM2 interactive segmentation**, **SAM3 open-vocabulary concept segmentation**, and **in-loop SAM2 LoRA fine-tuning** in a single web workspace.

### Who is Anvil built for?

Computer vision and data operations teams working on specialized visual domains—manufacturing defect inspection, retail quality control, agriculture, medical imaging, and document layout—where off-the-shelf foundation models underperform on domain-specific edge cases and data cannot leave private infrastructure.

### How does Anvil compare to open-source tools (CVAT, Label Studio, X-AnyLabeling, Labelme) and commercial SaaS (V7 Darwin, Roboflow, Labelbox)?

- **CVAT & Label Studio** are general-purpose annotation platforms. Using SAM2 or SAM3 with them requires deploying and maintaining separate Nuclio serverless functions or external ML backend containers, and neither includes built-in mask-decoder LoRA fine-tuning, adapter versioning, or side-by-side A/B IoU comparison.
- **X-AnyLabeling & Labelme** are single-user desktop applications. They are convenient for local file labeling but lack multi-user web collaboration, role-based access control (RBAC), review state machines, and server-side training loops.
- **V7 Darwin, Roboflow & Labelbox** are proprietary cloud SaaS products priced around per-seat subscriptions and usage/training credits, requiring data upload to vendor cloud storage unless under custom enterprise on-premises contracts.
- **Anvil** runs entirely on your own hardware via `docker compose`, ships both **SAM2** and **SAM3** behind a unified memory-governed provider registry, and trains domain-specific **SAM2 LoRA adapters** directly from approved project annotations.

### Why does in-loop fine-tuning matter if SAM2 and SAM3 are already available?

Zero-shot SAM2 and SAM3 perform well on common everyday objects (`truck`, `wheel`, `person`), but confidence and boundary precision degrade on specialized industrial or agricultural targets—such as subtle surface bruises, sunken defects, or cluttered bins.

In Anvil, you can use **SAM3** (`Find all ✨` or point/box prompts) and **SAM2** to bootstrap initial labels, curate them in Workview, and train a **SAM2 LoRA adapter** (`~47K` trainable parameters) in seconds to minutes. Subsequent interactive labeling and uncertainty ranking then use your domain-adapted model.

### How do SAM2 and SAM3 differ inside Anvil?

| Capability | SAM2 (`sam2.1_hiera_b+`) | SAM3 (`facebook/sam3`) |
| --- | --- | --- |
| **Primary strength** | Fast interactive point/box refinement + LoRA fine-tuning | Zero-shot open-vocabulary text concept discovery (`Find all ✨`) + point/box prompts |
| **Parameters / memory** | ~80M params (~2.1 GB RSS) | ~840M params (~3.5 GB RSS in fp32) |
| **Checkpoint delivery** | Public URL; auto-downloaded on `docker compose up` | Gated Hugging Face repo; enabled via `hf auth login && make sam3-setup` |
| **Encoder caching** | Pre-computed `.npz` per image on upload | On-demand via Hugging Face Transformers |
| **In-app LoRA fine-tuning** | Supported today (`~200 KB` adapter `.pt`) | Bootstraps annotations for SAM2 LoRA today; cross-attention adapter roadmap documented in [FINE_TUNING.md](FINE_TUNING.md) |

### Why aren't SAM3 weights downloaded automatically like SAM2?

Meta distributes `facebook/sam3` as a gated repository on Hugging Face that requires accepting their license terms under your own account. Anvil mounts your host Hugging Face cache (`~/.cache/huggingface` → `/hf-cache`) into the `ml` container so weights are never baked into Docker images or re-downloaded across rebuilds.

Run:

```bash
hf auth login
make sam3-setup
```

If SAM3 weights are not present, Anvil starts normally with SAM2 enabled.

### Does the stack run on a standard laptop?

Yes. The full stack (PostgreSQL, Redis, MinIO, API, ML, worker, and web UI) runs on a 16 GB Apple Silicon MacBook or Linux workstation. Anvil's model registry enforces `ANVIL_MODEL_MEMORY_BUDGET_GB` (default `6.0 GB`) and evicts the least-recently-used model when switching between SAM2 and SAM3 to prevent container OOM kills.

### How is SAM2 LoRA fine-tuning fast enough for CPU?

1. **Pre-computed encoder cache:** The worker runs SAM2's heavy Hiera image encoder once at upload time and stores `embeddings/<image_id>.npz`. The encoder never runs during training.
2. **Low-rank decoder adaptation:** Only `q_proj`, `k_proj`, `v_proj`, and `out_proj` in the mask decoder (~47K parameters at rank 8) are trained. See [FINE_TUNING.md](FINE_TUNING.md).

### What annotation geometries and taxonomy features are supported?

- **Geometries:** Polygon (with vertex/midpoint editing), Bounding Box (8-handle resize), Rotated Bounding Box (`OBB`), Polyline, Keypoints (with per-class skeleton graphs and occlusion states), and Brush (raster paint to contour polygon).
- **Taxonomy:** Multi-level class hierarchies (up to 5 levels) and per-class structured properties (`single`, `multi`, `text`).
- **Import / Export:** Drag-and-drop COCO ZIP import and one-click COCO ZIP export (preserving standard COCO segmentation/keypoints plus Anvil extensions for `obb`, `polyline`, and class hierarchy paths).

### How do review workflows and RBAC work?

Anvil enforces four roles (`admin`, `reviewer`, `labeler`, `viewer`). Images progress through `new → in_progress → submitted → approved / rejected`. Reviewers cannot approve or reject their own submissions, rejections require a documented reason, and spatial pin comments can be anchored directly to `(x, y)` image coordinates.

### What is the license?

- **Noncommercial use:** Available under the [PolyForm Noncommercial License 1.0.0](https://github.com/ssgaur/anvil-cv/blob/main/LICENSE).
- **Commercial use:** Any use in a commercial or for-profit business context requires a separate written paid license. Commercial agreements can be structured as annual/monthly subscriptions, per-seat or per-deployment licenses, usage-based tiers, or revenue share. See [COMMERCIAL_LICENSE.md](https://github.com/ssgaur/anvil-cv/blob/main/COMMERCIAL_LICENSE.md).

Copies previously received under Apache License 2.0 retain the rights granted with those copies; the PolyForm Noncommercial terms apply prospectively.

### How do I diagnose container or memory issues?

Run:

```bash
python3 scripts/diagnose.py
```

It inspects container states, exit codes (including `137` OOM kills), service readiness, memory pressure, failed image embeddings, and recent structured incident logs in `logs/anvil.jsonl`. See [OBSERVABILITY.md](OBSERVABILITY.md).
