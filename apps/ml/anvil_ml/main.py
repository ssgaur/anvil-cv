"""Anvil ML service.

Endpoints:
  GET  /health
  POST /embed    {image_path, embedding_path}
  POST /predict  {image_path, embedding_path?, points?, point_labels?, box?, adapter_path?}
  POST /train    {items: [...], adapter_path, config: {...}}  (sync; long-running)
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from anvil_ml import __version__
from anvil_ml.engine import device_name, embed, predict, score_uncertainty

app = FastAPI(title="Anvil ML", version=__version__)


class EmbedRequest(BaseModel):
    image_path: str
    embedding_path: str


class PredictRequest(BaseModel):
    image_path: str
    embedding_path: str | None = None
    points: list[list[float]] | None = None
    point_labels: list[int] | None = None
    box: list[float] | None = None
    adapter_path: str | None = None


class TrainItemModel(BaseModel):
    image_path: str
    embedding_path: str
    annotations: list[dict[str, Any]]


class TrainRequest(BaseModel):
    items: list[TrainItemModel]
    adapter_path: str
    epochs: int = 20
    lr: float = 1e-4
    rank: int = 8
    alpha: int = 16
    eval_split: float = 0.2
    device: str = "cpu"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "device": device_name()}


@app.post("/embed")
async def embed_endpoint(req: EmbedRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(embed, req.image_path, req.embedding_path)
    except FileNotFoundError as e:
        raise HTTPException(404, f"missing file: {e}") from e
    except Exception as e:
        raise HTTPException(500, f"embed failed: {e}") from e


@app.post("/predict")
async def predict_endpoint(req: PredictRequest) -> dict[str, Any]:
    if not req.points and not req.box:
        raise HTTPException(400, "either points or box is required")
    try:
        return await asyncio.to_thread(
            predict,
            req.image_path, req.embedding_path,
            req.points, req.point_labels, req.box,
            req.adapter_path,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, f"missing file: {e}") from e
    except Exception as e:
        raise HTTPException(500, f"predict failed: {e}") from e


@app.post("/train")
async def train_endpoint(req: TrainRequest) -> dict[str, Any]:
    """Synchronous LoRA fine-tune. Returns metrics on completion.

    Caller is responsible for running this in a background task — the request
    will block for the duration of training (potentially minutes).
    """
    from anvil_ml.training import TrainConfig, TrainItem, train_lora

    items = [TrainItem(**it.model_dump()) for it in req.items]
    cfg = TrainConfig(
        epochs=req.epochs, lr=req.lr, rank=req.rank, alpha=req.alpha,
        eval_split=req.eval_split, device=req.device,
    )
    try:
        return await asyncio.to_thread(train_lora, items, req.adapter_path, cfg)
    except Exception as e:
        raise HTTPException(500, f"train failed: {e}") from e


class ScoreRequest(BaseModel):
    image_path: str
    embedding_path: str | None = None
    adapter_path: str | None = None


@app.post("/score")
async def score_endpoint(req: ScoreRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            score_uncertainty, req.image_path, req.embedding_path, req.adapter_path,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, f"missing file: {e}") from e
    except Exception as e:
        raise HTTPException(500, f"score failed: {e}") from e
