# Anvil

> Self-hosted computer vision annotation platform with **SAM2 interactive segmentation**, **SAM3 open-vocabulary concept segmentation**, and **in-loop LoRA model adaptation**.

[![CI](https://github.com/ssgaur/anvil-cv/actions/workflows/ci.yml/badge.svg)](https://github.com/ssgaur/anvil-cv/actions/workflows/ci.yml)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm_Noncommercial_1.0.0-orange.svg)](LICENSE)
[![Commercial License](https://img.shields.io/badge/commercial-paid_license_required-0f766e.svg)](COMMERCIAL_LICENSE.md)

Anvil is a self-hosted annotation and model-in-the-loop platform built for engineering teams working on domain-specific computer vision—manufacturing inspection, retail quality control, agriculture, medical imaging, and document analysis.

Most labeling tools treat foundation models as static pre-labeling plugins. Anvil closes the loop inside the annotation workspace:

1. **Bootstrap with SAM3 (`facebook/sam3`)** — segment every matching instance across an image using open-vocabulary text concepts (with tunable confidence thresholds) or direct point/box prompts.
2. **Refine with SAM2 (`sam2.1_hiera_b+`)** — sub-second multi-point and box interactive segmentation backed by pre-computed disk embeddings.
3. **Specialize in-loop with LoRA** — train lightweight (~200 KB, ~47K parameter) mask-decoder LoRA adapters directly on your approved annotations without a GPU cluster, evaluate per-class IoU deltas with side-by-side A/B comparisons, and rank unlabeled images by model uncertainty.

Runs on a single **Apple Silicon laptop (16 GB RAM)** or an on-premises Linux/GPU server with zero external telemetry.

![Anvil workflow demo](docs/assets/anvil-demo.gif)

**Data & privacy guarantee:** No telemetry, no usage analytics, and no outbound calls beyond model weight downloads you initiate. Images, annotations, embeddings, and trained adapters remain entirely on your infrastructure.

---

## Ecosystem Comparison

How Anvil compares across **open-source / self-hosted tools** and **commercial cloud platforms**:

| Capability | CVAT | Label Studio | X-AnyLabeling / Labelme | V7 Darwin / Roboflow / Labelbox | **Anvil** |
| --- | --- | --- | --- | --- | --- |
| **Deployment model** | Self-hosted / Cloud | Self-hosted / Cloud | Desktop app (single-user) | Cloud SaaS (on-prem enterprise only) | **Self-hosted web platform (`docker compose`)** |
| **Source code access** | Open source (MIT) | Open core (Apache 2.0) | Open source (GPL / MIT) | Proprietary | **Source-available ([PolyForm Noncommercial](LICENSE) + [Commercial](COMMERCIAL_LICENSE.md))** |
| **SAM2 interactive segmentation** | Via Nuclio serverless setup | Via external ML backend container | Local ONNX / desktop | Hosted cloud inference | **Built-in (pre-computed `.npz` embedding cache)** |
| **SAM3 open-vocabulary concept + prompt segmentation** | Manual / custom integration | External backend required | Desktop config only | Cloud-dependent | **Built-in (Workview model switcher + concept bar)** |
| **In-loop SAM2 LoRA fine-tuning** | No | No | No | Cloud training credits / external | **Built-in (~47K params, runs on CPU/MPS/CUDA)** |
| **A/B model evaluation (per-class IoU + delta)** | No | No | No | Partial | **Built-in** |
| **Active learning (entropy-ranked queue)** | No | Enterprise / external | No | Paid tier | **Built-in** |
| **Team RBAC, review state machine & spatial comments** | Yes | Partial (Enterprise for full RBAC) | No | Yes | **Built-in (`admin`, `reviewer`, `labeler`, `viewer`)** |
| **Telemetry-free by default** | Yes | Configurable | Yes | No | **Yes** |
| **Commercial usage terms** | Permissive | Enterprise tier for team features | Copyleft / desktop | Per-seat + usage/credit tiers | **[Commercial license agreement](COMMERCIAL_LICENSE.md)** |

---

## Core Capabilities

- **Annotation Workview** — Polygon, Bounding Box (8-handle resize), Rotated Bounding Box (OBB), Polyline, Keypoints (with per-class skeleton schema and occlusion flags), Brush (raster mask → contour polygon), vertex insertion/deletion, hold-Space pan, 20-step undo/redo, and keyboard shortcut cheatsheet.
- **Dual Foundation Model Support (SAM2 + SAM3)** — Switch between **base SAM2**, **base SAM3**, or any **fine-tuned project LoRA adapter** directly in the Workview top bar:
  - **SAM2 (`sam2.1_hiera_b+`):** Multi-point positive/negative prompts and box prompts against pre-computed image embeddings (~80–200 ms after warm-up).
  - **SAM3 (`facebook/sam3`):** Open-vocabulary text concept segmentation (`Find all ✨` with adjustable confidence threshold) plus interactive point/box prompts on the same canvas.
- **Memory-Governed Model Registry** — Lazy loading and automatic LRU eviction governed by `ANVIL_MODEL_MEMORY_BUDGET_GB` (default `6.0 GB`) so SAM2 (~2.1 GB) and SAM3 (~3.5 GB) can run on a 16 GB machine without container OOM kills.
- **In-Loop SAM2 LoRA Fine-Tuning** — Freezes the Hiera image encoder (replaying cached `.npz` embeddings from disk) and trains rank-8 LoRA adapters on mask-decoder attention projections (`q_proj`, `k_proj`, `v_proj`, `out_proj`). Produces ~200 KB `.pt` adapters in seconds to minutes on CPU.
- **Model Versioning & A/B Evaluation** — Tracks per-epoch validation IoU, per-class IoU bars, and side-by-side A/B evaluation across held-out project annotations before promoting a version to active.
- **Active Learning Queue** — `Next uncertain ✨` scores unlabeled images using 3×3 grid mask-score entropy against the active model and routes labelers to the highest-uncertainty samples first.
- **Review & Quality Workflow** — `new → in_progress → submitted → approved / rejected` state machine with self-review prevention, required rejection reasons, and spatial pin comments anchored to image coordinates.
- **Taxonomy & Class Properties** — Hierarchical classes (up to 5 levels), keypoint skeleton templates, WCAG-AA color palette, and per-annotation structured attributes (`single`, `multi`, `text`).
- **Dataset Operations & Interoperability** — Server-side filtering and sorting, shift-range multi-select, bulk review/move/delete, and lossless COCO ZIP import/export (including Anvil extensions for polylines, OBB, keypoints, and class hierarchies).
- **Enterprise-Ready Auth & Operations** — First-run `/setup` wizard, RBAC, HttpOnly cookie sessions with rotating refresh tokens and reuse detection, CSRF protection, GitHub/Google OAuth, rate limiting, `/ready` and `/metrics` endpoints, structured JSON incident logs, and `python3 scripts/diagnose.py`.

---

## Quick Start

### 1. Start the stack (SAM2 ready out of the box)

Requires **Docker** (Docker Desktop, Rancher Desktop, or Docker Engine with Compose).

```bash
git clone https://github.com/ssgaur/anvil-cv.git
cd anvil-cv
cp .env.example .env
docker compose up -d --build
```

Open **<http://localhost:5173>**.

- On first run, the `init-checkpoint` service automatically downloads the public **SAM2.1 Base+** checkpoint (`sam2.1_hiera_base_plus.pt`, ~700 MB) into a persistent Docker volume.
- Default development credentials: `admin@anvil.dev` / `admin`.

To seed a sample project with starter images, classes, and annotations:

```bash
python3 scripts/load_sample_dataset.py
```

### 2. Enable SAM3 (optional, 2 commands)

Meta distributes `facebook/sam3` as a **gated repository on Hugging Face**. Anvil mounts your host Hugging Face cache (`~/.cache/huggingface` → `/hf-cache`) into the `ml` container so weights are downloaded once and never baked into Docker images.

1. Request/accept access on Hugging Face: **<https://huggingface.co/facebook/sam3>**
2. Authenticate on your host (or set `HF_TOKEN=hf_...` in `.env`) and pull only the required Transformers weights (`model.safetensors`, ~3.2 GB, skipping the duplicate 3.2 GB `sam3.pt` file):

```bash
hf auth login
make sam3-setup
```

Once complete, open any image in Workview, select **`Model: base SAM3`** in the top bar, and use either **Auto-Annotate (`✨`)** point/box prompts or the **SAM3 concept bar** (`Find all ✨`). If SAM3 is not configured, Anvil degrades gracefully to SAM2 without blocking startup.

### Common Commands

```bash
make up                  # Start all services (creates .env if missing)
make sam3-setup          # Pre-download facebook/sam3 safetensors into shared HF cache
python3 scripts/diagnose.py  # Run self-diagnostics across containers, health, and logs
make logs                # Tail service logs
make ps                  # Check container health
make down                # Stop containers (preserves data volumes)
make clean               # Stop containers and remove all volumes
```

| Endpoint | Description |
| --- | --- |
| `http://localhost:5173` | Anvil Web UI |
| `http://localhost:8000` | FastAPI REST service + OpenAPI (`/docs`) |
| `http://localhost:8001` | PyTorch ML service (`/health`, `/capabilities`, `/predict`, `/predict_concept`, `/train`) |
| `http://localhost:9001` | MinIO console (local object storage) |

---

## Verification

Verify a fresh installation end-to-end:

```bash
python3 scripts/diagnose.py
curl -fsS http://localhost:8000/ready
curl -fsS http://localhost:8001/capabilities
python3 scripts/load_sample_dataset.py
```

---

## Repository Structure

```
anvil-cv/
├── apps/
│   ├── web/        React + TypeScript + Konva annotation workspace
│   ├── api/        FastAPI + SQLModel + PostgreSQL REST API & auth
│   ├── ml/         FastAPI + PyTorch model service (SAM2, SAM3, LoRA training)
│   └── worker/     Arq background worker (SAM2 embedding pre-computation)
├── packages/
│   └── storage/    Pluggable storage backends (Local, MinIO, S3, Azure, GCS)
├── docs/           Architecture, model adaptation, SAM3 integration, API & ops docs
├── scripts/        End-to-end verification tests, diagnostics, and dataset utilities
└── vendor/sam2/    Vendored SAM2 package (Apache-2.0; see vendor/sam2/UPSTREAM.md)
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — service topology, encoder cache, and request flows
- [Features](docs/FEATURES.md) — annotation tools, review workflows, and shortcuts
- [Fine-Tuning & Model Adaptation](docs/FINE_TUNING.md) — SAM2 LoRA architecture and SAM3 + SAM2 workflows
- [SAM3 Integration](docs/SAM3_INTEGRATION.md) — provider abstraction, memory budget, and Hugging Face setup
- [Observability & Diagnostics](docs/OBSERVABILITY.md) — `diagnose.py`, structured logs, and OOM prevention
- [API Reference](docs/API.md) — REST endpoints
- [Local Development Setup](docs/DEV_SETUP.md) — bare-metal 3-process workflow
- [FAQ](docs/FAQ.md) — technical, deployment, and licensing questions
- [Roadmap](docs/ROADMAP.md) & [Paid Product Roadmap](docs/PAID_PRODUCT_ROADMAP.md)

## Community & Governance

- [Commercial Licensing](COMMERCIAL_LICENSE.md)
- [Contributing](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Changelog](CHANGELOG.md)

## License

Anvil is distributed under a dual-license model:

- **Noncommercial use:** governed by the [PolyForm Noncommercial License 1.0.0](LICENSE). Free for personal research, academic use, evaluation, and qualifying noncommercial organizations.
- **Commercial use:** any use in a commercial or for-profit context—including internal business operations, commercial dataset creation, model training for commercial products, or hosted offerings—requires a separate written commercial license. See [COMMERCIAL_LICENSE.md](COMMERCIAL_LICENSE.md) to request commercial terms.

This is a **source-available** repository, not OSI-approved open source. Third-party components, including `vendor/sam2/`, remain under their respective upstream licenses (see [NOTICE](NOTICE)). Copies previously received under Apache License 2.0 retain their original grant; these terms apply prospectively.
