"""Anvil ML service.

Endpoints:
  GET  /health
  POST /embed    {image_path, embedding_path}
  POST /predict  {image_path, embedding_path?, points?, point_labels?, box?}
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from anvil_ml import __version__
from anvil_ml.engine import device_name, embed, predict

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "device": device_name()}


@app.post("/embed")
async def embed_endpoint(req: EmbedRequest) -> dict[str, Any]:
    try:
        # Run in threadpool — SAM2 calls are blocking torch ops
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
        )
    except FileNotFoundError as e:
        raise HTTPException(404, f"missing file: {e}") from e
    except Exception as e:
        raise HTTPException(500, f"predict failed: {e}") from e
