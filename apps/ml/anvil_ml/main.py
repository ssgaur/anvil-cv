"""Anvil ML service.

Endpoints:
  GET  /health
  POST /embed    {image_path, embedding_path}
  POST /predict  {image_path, embedding_path?, points?, point_labels?, box?, adapter_path?}
  POST /train    {items: [...], adapter_path, config: {...}}  (sync; long-running)
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from anvil_ml import __version__
from anvil_ml.engine import device_name, embed, predict, score_uncertainty
from anvil_ml.obs import configure_logging, memory_report

log = logging.getLogger("anvil.ml")

# Warn before the kernel kills us; an OOM kill itself can never be logged.
_MEMORY_WARN_PCT = 85.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(level="INFO")
    log.info(
        "ml_service_start",
        extra={"version": __version__, "device": device_name(), **memory_report()},
    )
    yield


app = FastAPI(title="Anvil ML", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every ML call with timing + memory, so a later OOM has context.

    A container that is OOM-killed dies without unwinding, so the last line
    emitted here is the best evidence of what killed it.
    """
    if request.url.path == "/health":
        return await call_next(request)

    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as e:
        log.exception(
            "ml_request_failed",
            extra={
                "path": request.url.path,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                "error": f"{type(e).__name__}: {e}"[:500],
                **memory_report(),
            },
        )
        raise

    mem = memory_report()
    duration_ms = round((time.monotonic() - started) * 1000, 2)
    log.info(
        "ml_request",
        extra={
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            **mem,
        },
    )
    used_pct = mem.get("memory_used_pct")
    if used_pct is not None and used_pct >= _MEMORY_WARN_PCT:
        log.warning(
            "incident:ml_memory_pressure",
            extra={
                "code": "ml_memory_pressure",
                "component": "ml",
                "operation": request.url.path,
                "probable_cause": (
                    f"ML container is using {used_pct}% of its memory limit "
                    f"({mem.get('memory_used_mb')}MB of {mem.get('memory_limit_mb')}MB). "
                    "SAM2 is close to being OOM-killed."
                ),
                "remediation": (
                    "Raise the Docker/Rancher VM memory to at least 8GB (10GB "
                    "recommended), then `docker compose up -d`. If already high, "
                    "use a smaller SAM2 variant."
                ),
                **mem,
            },
        )
    return response


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
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "device": device_name(),
        **memory_report(),
    }


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


class EvalRequest(BaseModel):
    items: list[TrainItemModel]
    adapter_path: str | None = None  # null = base SAM2
    device: str = "cpu"


@app.post("/evaluate")
async def evaluate_endpoint(req: EvalRequest) -> dict[str, Any]:
    """Run eval-only (no training) on the given items, optionally with an adapter."""
    from anvil_ml.training import TrainItem, evaluate_with_adapter

    items = [TrainItem(**it.model_dump()) for it in req.items]
    try:
        return await asyncio.to_thread(evaluate_with_adapter, items, req.adapter_path, req.device)
    except FileNotFoundError as e:
        raise HTTPException(404, f"missing file: {e}") from e
    except Exception as e:
        raise HTTPException(500, f"evaluate failed: {e}") from e


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
