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
import os
import secrets
import subprocess
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from anvil_ml import __version__
from anvil_ml.engine import device_name, embed, predict, score_uncertainty
from anvil_ml.obs import configure_logging, memory_report
from anvil_ml.providers import (
    CapabilityError,
    capabilities_of,
    get_provider,
    list_capabilities,
)
from anvil_ml.providers import release as release_provider
from anvil_ml.video_showcase import (
    discover_frame,
    prepare_video,
)
from anvil_ml.video_showcase import (
    showcase_ready as video_showcase_ready,
)

log = logging.getLogger("anvil.ml")

# Warn before the kernel kills us; an OOM kill itself can never be logged.
_MEMORY_WARN_PCT = 85.0
_sam3_inference_gate = asyncio.Semaphore(1)


def require_internal(
    x_anvil_internal: str | None = Header(default=None),
) -> None:
    expected = os.getenv("ANVIL_ML_INTERNAL_TOKEN", "anvil-local-ml")
    if not x_anvil_internal or not secrets.compare_digest(x_anvil_internal, expected):
        raise HTTPException(403, "internal ML credentials required")


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


class PrepareVideoRequest(BaseModel):
    video_id: str


class DiscoverFrameRequest(BaseModel):
    video_id: str
    timestamp_s: float
    concept: str
    threshold: float = 0.3


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "device": device_name(),
        **memory_report(),
    }


@app.get("/showcase/ready")
def showcase_ready_endpoint() -> dict[str, Any]:
    return video_showcase_ready()


@app.post("/video/prepare", dependencies=[Depends(require_internal)])
async def prepare_video_endpoint(req: PrepareVideoRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(prepare_video, req.video_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"missing video: {exc}") from exc
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise HTTPException(400, f"video preparation failed: {exc}") from exc


@app.post("/video/discover", dependencies=[Depends(require_internal)])
async def discover_frame_endpoint(req: DiscoverFrameRequest) -> dict[str, Any]:
    try:
        async with _sam3_inference_gate:
            return await asyncio.to_thread(
                discover_frame,
                req.video_id,
                req.timestamp_s,
                req.concept,
                req.threshold,
            )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, f"object discovery failed: {exc}") from exc


@app.get("/capabilities")
def capabilities_endpoint() -> dict[str, Any]:
    """What each model can be prompted with.

    The frontend reads this instead of assuming every model accepts the same
    prompts — SAM2 has no text encoder, SAM3 has no point/box refinement here.
    """
    return list_capabilities()


class ConceptRequest(BaseModel):
    image_path: str
    text: str
    model: str = "sam3"
    threshold: float = 0.5
    max_instances: int = 100


@app.post("/predict_concept")
async def predict_concept_endpoint(req: ConceptRequest) -> dict[str, Any]:
    """Text-prompted segmentation returning EVERY matching instance.

    An empty `instances` list means the model found nothing. That is NOT the
    same as "verified absent" — only a human can assert that, so the caller
    must not treat this as a negative training example on its own.
    """
    try:
        caps = capabilities_of(req.model)
    except KeyError as e:
        # str(KeyError) wraps the message in quotes; use the raw arg.
        raise HTTPException(404, e.args[0] if e.args else "unknown model") from e

    # Check capability BEFORE loading: otherwise asking SAM2 for a text prompt
    # would pull ~2.1GB into memory only to reject the request.
    if not caps.text:
        raise HTTPException(
            400,
            f"model '{req.model}' does not support text prompts; "
            "use point/box prompts or select a text-capable model",
        )

    provider = get_provider(req.model)

    try:
        instances = await asyncio.to_thread(
            provider.predict_concept,
            req.image_path,
            req.text,
            req.threshold,
            req.max_instances,
        )
    except CapabilityError as e:
        raise HTTPException(400, str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(404, f"missing file: {e}") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"predict_concept failed: {e}") from e

    return {
        "model": req.model,
        "text": req.text,
        "threshold": req.threshold,
        "instances": [i.to_dict() for i in instances],
        "count": len(instances),
    }


@app.post("/models/{name}/release")
def release_model_endpoint(name: str) -> dict[str, Any]:
    """Free a model's memory. Useful on laptop-sized deployments."""
    return {"released": release_provider(name), "model": name}


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
