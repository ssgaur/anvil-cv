# Anvil

> Open-source image annotation platform with **built-in SAM2 fine-tuning**.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange)]()

Anvil is an alternative to V7 Labs Darwin, CVAT, and Roboflow — but the model **learns from your team as you label**. Annotate a few hundred images, click *Fine-tune*, and the next batch is auto-labeled by your domain-specific model. Active learning surfaces the highest-uncertainty images first so every label counts more.

## Why Anvil

| | V7 Darwin | CVAT | Roboflow | **Anvil** |
|--|--|--|--|--|
| Open source | ❌ | ✅ | partial | ✅ Apache 2.0 |
| Built-in SAM2 fine-tuning | ❌ | ❌ | ❌ | ✅ |
| Active learning queue | ❌ | ❌ | partial | ✅ |
| Self-hostable, single command | ❌ | ✅ | ❌ | ✅ |
| Embedding pre-compute (instant click-to-segment) | ✅ | ❌ | ✅ | ✅ |
| Per-seat pricing | $500/mo | free | $$ | **free** |

## Quick start (dev)

```bash
git clone https://github.com/ssgaur/anvil-cv.git
cd anvil-cv
cp .env.example .env
docker compose up
```

Open http://localhost:5173

## Repo layout

```
anvil-cv/
├── apps/
│   ├── web/        Vite + React + TS frontend
│   ├── api/        FastAPI REST API + Postgres
│   ├── ml/         FastAPI + SAM2 inference & fine-tuning
│   └── worker/     Arq background jobs (embeddings, training, exports)
├── packages/
│   └── storage/    Pluggable storage (Local / S3 / Azure / GCS / MinIO)
├── docs/           Documentation site
└── docker-compose.yml
```

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md). v0 ships annotation loop + fine-tune button + active learning + COCO/YOLO export.

## License

Apache 2.0 — see [LICENSE](LICENSE).
