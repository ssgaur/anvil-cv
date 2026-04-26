"""Anvil background worker (arq).

Jobs:
  - precompute_embedding(image_id): runs SAM2 image encoder, persists embedding to storage
  - generate_thumbnail(image_id): 256px JPEG thumbnail
  - finetune(model_version_id): LoRA fine-tune driver
  - export_dataset(export_id): COCO/YOLO/etc

v0.0.1: stubs only.
"""

import os

from arq.connections import RedisSettings


async def precompute_embedding(_ctx: dict, image_id: str) -> dict[str, str]:
    return {"image_id": image_id, "status": "stub"}


async def generate_thumbnail(_ctx: dict, image_id: str) -> dict[str, str]:
    return {"image_id": image_id, "status": "stub"}


async def startup(_ctx: dict) -> None:
    print("[anvil-worker] started")


async def shutdown(_ctx: dict) -> None:
    print("[anvil-worker] stopping")


class WorkerSettings:
    functions = [precompute_embedding, generate_thumbnail]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(
        os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
