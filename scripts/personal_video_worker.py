#!/usr/bin/env python3
"""Native-Mac personal worker for SAM2 tracking + ProPainter removal.

Run outside Docker so PyTorch can use Apple MPS:

    python3 scripts/personal_video_worker.py

This backend is intentionally optional and non-commercial. ProPainter source
and weights must be installed with `install_propainter_personal.py`; nothing
from ProPainter is redistributed with Anvil.
"""

from __future__ import annotations

import base64
import contextlib
import gc
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any
from uuid import UUID

# Prefer a controlled PyTorch OOM (which becomes a resumable interrupted job)
# over allowing MPS to pressure the whole 16GB Mac into a forced shutdown.
os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.70")
os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", "0.50")

import cv2
import numpy as np
import torch
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pycocotools import mask as mask_util
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
PERSONAL = ROOT / ".anvil-personal"
PROPAINTER = PERSONAL / "ProPainter"
JOBS_ROOT = Path(
    os.getenv("ANVIL_PERSONAL_JOBS_ROOT", str(PERSONAL / "jobs"))
).resolve()
SAM2_CHECKPOINT = ROOT / "checkpoints" / "sam2.1_hiera_base_plus.pt"
SAM2_CONFIG = "configs/sam2.1/sam2.1_hiera_b+.yaml"
TOKEN = os.getenv("ANVIL_PERSONAL_WORKER_TOKEN", "anvil-personal-worker")

app = FastAPI(title="Anvil personal video worker")
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_job_gate = threading.Semaphore(1)
_cancel_events: dict[str, threading.Event] = {}
_stop_modes: dict[str, str] = {}

if torch.backends.mps.is_available():
    torch.mps.set_per_process_memory_fraction(0.60)


class RemovalJobIn(BaseModel):
    job_id: str
    source_url: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    seed_s: float = Field(ge=0)
    mask_rles: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    manual_mask_png: str | None = Field(default=None, max_length=4_000_000)
    fps: int = Field(default=30, ge=8, le=30)
    # 432x240 is the measured ceiling for a 16GB Mac, not a model limit. The
    # caps are wide enough for a bigger GPU; see _processing_size() and
    # docs/PERSONAL_VIDEO_REMOVAL.md for the benchmark behind the default.
    width: int = Field(default=432, ge=320, le=1920)
    height: int = Field(default=240, ge=176, le=1080)


def require_token(x_anvil_personal: str | None = Header(default=None)) -> None:
    import secrets

    if not x_anvil_personal or not secrets.compare_digest(x_anvil_personal, TOKEN):
        raise HTTPException(403, "personal worker credentials required")


def _persist_locked(job_id: str) -> None:
    job_dir = JOBS_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    temp = job_dir / "status.json.tmp"
    temp.write_text(json.dumps(_jobs[job_id]))
    temp.replace(job_dir / "status.json")


def _set(job_id: str, **values: Any) -> None:
    with _jobs_lock:
        event = _cancel_events.get(job_id)
        if event is not None and event.is_set():
            return
        row = _jobs.setdefault(job_id, {})
        row.update(values)
        row["last_update_at"] = time.time()
        _persist_locked(job_id)


def _heartbeat(job_id: str, interval_s: float = 5.0) -> None:
    with _jobs_lock:
        row = _jobs.get(job_id)
        if not row or row.get("status") not in {"running", "pausing"}:
            return
        now = time.time()
        if now - float(row.get("last_update_at") or 0) < interval_s:
            return
        row["last_update_at"] = now
        _persist_locked(job_id)


def _start_attempt(job_id: str) -> None:
    with _jobs_lock:
        row = _jobs[job_id]
        now = time.time()
        row.setdefault("active_seconds", 0.0)
        row["attempt_started_at"] = now
        row["last_update_at"] = now
        row["status"] = "running"
        _persist_locked(job_id)


def _finish_attempt(job_id: str) -> None:
    with _jobs_lock:
        row = _jobs.get(job_id)
        if not row:
            return
        started = row.get("attempt_started_at")
        if started is not None:
            row["active_seconds"] = float(row.get("active_seconds") or 0) + max(
                0.0, time.time() - float(started)
            )
        row["attempt_started_at"] = None
        row["last_update_at"] = time.time()
        _persist_locked(job_id)


def _freeze_crashed_attempt(job_id: str) -> None:
    """Count work only through the last heartbeat, excluding machine downtime."""
    with _jobs_lock:
        row = _jobs.get(job_id)
        if not row:
            return
        started = row.get("attempt_started_at")
        if started is not None:
            ended = float(row.get("last_update_at") or started)
            row["active_seconds"] = float(row.get("active_seconds") or 0) + max(
                0.0, ended - float(started)
            )
        row["attempt_started_at"] = None
        _persist_locked(job_id)


def _status(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        row = _jobs.get(job_id)
        if row is None:
            persisted = JOBS_ROOT / job_id / "status.json"
            if persisted.is_file():
                row = json.loads(persisted.read_text())
                _jobs[job_id] = row
        if not row:
            raise KeyError(job_id)
        result = dict(row)
        started = result.get("attempt_started_at")
        if started is not None and result.get("status") in {"running", "pausing"}:
            result["active_seconds"] = float(result.get("active_seconds") or 0) + max(
                0.0, time.time() - float(started)
            )
        return result


def _check_install() -> tuple[bool, str | None]:
    if not (PERSONAL / "PROPAINTER_PERSONAL_USE_ONLY").is_file():
        return False, "personal-use license has not been accepted"
    required = (
        PROPAINTER / "inference_propainter.py",
        PROPAINTER / "weights" / "ProPainter.pth",
        PROPAINTER / "weights" / "recurrent_flow_completion.pth",
        PROPAINTER / "weights" / "raft-things.pth",
        SAM2_CHECKPOINT,
    )
    missing = [str(path) for path in required if not path.is_file()]
    return (not missing), (f"missing: {', '.join(missing)}" if missing else None)


@app.get("/health")
def health() -> dict[str, Any]:
    installed, error = _check_install()
    return {
        "status": "ok" if installed else "not_ready",
        "propainter": installed,
        "error": error,
        "device": "mps" if torch.backends.mps.is_available() else "cpu",
        "personal_use_only": True,
        "max_clip_s": 600,
        "processing_size": [432, 240],
        "resumable": True,
        "tracking_checkpoint_max_frames": 60,
        "mps_memory_fraction": 0.60,
    }


def _processing_size(body: RemovalJobIn) -> RemovalJobIn:
    """Apply the operator's ProPainter working resolution, if they set one.

    ProPainter's output is composited into the mask at full source resolution,
    so this value alone decides how sharp the reconstructed region looks: at
    the 432x240 default a generated pixel is stretched over ~20 output pixels.

    Raising it is a hardware question, not a quality preference. Benchmarks on
    a 16GB M1 Pro (documented in docs/PERSONAL_VIDEO_REMOVAL.md) show 432x240
    is the practical ceiling there; 512x288 and 640x360 either thrash or run
    several times slower. On a larger GPU set ANVIL_PROCESS_SIZE=1024x576 (or
    1920x1080 to remove the upscale entirely).
    """
    raw = os.getenv("ANVIL_PROCESS_SIZE", "").strip().lower()
    if not raw:
        return body
    try:
        width_text, _, height_text = raw.partition("x")
        width, height = int(width_text), int(height_text)
    except ValueError as exc:
        raise HTTPException(400, f"invalid ANVIL_PROCESS_SIZE={raw!r}") from exc
    # ProPainter silently truncates each side down to a multiple of 8
    # (resize_frames in inference_propainter.py). Rejecting unaligned sizes
    # keeps the processed aspect ratio identical to the source, instead of
    # stretching the reconstruction by a few pixels during compositing.
    if width % 8 or height % 8:
        raise HTTPException(
            400,
            f"ANVIL_PROCESS_SIZE={raw} must be a multiple of 8 on both sides "
            "(e.g. 1024x576, 1920x1080)",
        )
    return body.model_copy(update={"width": width, "height": height})


@app.post("/jobs", dependencies=[Depends(require_token)])
def create_job(body: RemovalJobIn) -> dict[str, Any]:
    installed, error = _check_install()
    if not installed:
        raise HTTPException(503, error)
    body = _processing_size(body)
    job_id = str(UUID(body.job_id))
    if body.end_s <= body.start_s:
        raise HTTPException(400, "end_s must be after start_s")
    if body.end_s - body.start_s > 600.01:
        raise HTTPException(400, "personal local removal is limited to 10-minute videos")
    if not body.start_s <= body.seed_s <= body.end_s:
        raise HTTPException(400, "selected object frame must be inside the clip range")
    with _jobs_lock:
        if job_id in _jobs:
            raise HTTPException(409, "job already exists")
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "error": None,
            "output_ready": False,
            "active_seconds": 0.0,
            "attempt_started_at": None,
            "last_update_at": time.time(),
            "created_at": time.time(),
        }
        _cancel_events[job_id] = threading.Event()
        job_dir = JOBS_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "input.json").write_text(body.model_dump_json())
    _set(job_id)
    threading.Thread(
        target=_run_job,
        args=(job_id, body),
        daemon=True,
        name=f"removal-{job_id}",
    ).start()
    return _status(job_id)


@app.get("/jobs/{job_id}", dependencies=[Depends(require_token)])
def get_job(job_id: str) -> dict[str, Any]:
    try:
        return _status(str(UUID(job_id)))
    except KeyError as exc:
        raise HTTPException(404, "job not found") from exc


@app.get("/jobs/{job_id}/output", dependencies=[Depends(require_token)])
def get_output(job_id: str) -> FileResponse:
    canonical = str(UUID(job_id))
    try:
        row = _status(canonical)
    except KeyError as exc:
        raise HTTPException(404, "job not found") from exc
    output = JOBS_ROOT / canonical / "removed.mp4"
    if row["status"] != "completed" or not output.is_file():
        raise HTTPException(409, "output is not ready")
    return FileResponse(output, media_type="video/mp4", filename="object-removed.mp4")


@app.delete("/jobs/{job_id}", dependencies=[Depends(require_token)])
def delete_job(job_id: str) -> None:
    canonical = str(UUID(job_id))
    with _jobs_lock:
        _stop_modes[canonical] = "cancel"
        _cancel_events.setdefault(canonical, threading.Event()).set()
        _jobs.pop(canonical, None)
    # The running thread owns cleanup; for an idle/completed job, clean now.
    if not any(
        thread.name == f"removal-{canonical}" and thread.is_alive()
        for thread in threading.enumerate()
    ):
        shutil.rmtree(JOBS_ROOT / canonical, ignore_errors=True)
        with _jobs_lock:
            _cancel_events.pop(canonical, None)
            _stop_modes.pop(canonical, None)


@app.post("/jobs/{job_id}/pause", dependencies=[Depends(require_token)])
def pause_job(job_id: str) -> dict[str, Any]:
    canonical = str(UUID(job_id))
    with _jobs_lock:
        row = _jobs.get(canonical)
        if not row:
            raise HTTPException(404, "job not found")
        if row["status"] not in {"queued", "running"}:
            return dict(row)
        _stop_modes[canonical] = "pause"
        _cancel_events.setdefault(canonical, threading.Event()).set()
        _jobs[canonical].update(
            status="pausing",
            stage="pausing",
            detail="Finishing the current operation and saving completed checkpoints",
        )
        _jobs[canonical]["last_update_at"] = time.time()
        _persist_locked(canonical)
    return _status(canonical)


@app.post("/jobs/{job_id}/resume", dependencies=[Depends(require_token)])
def resume_job(job_id: str) -> dict[str, Any]:
    canonical = str(UUID(job_id))
    input_file = JOBS_ROOT / canonical / "input.json"
    if not input_file.is_file():
        raise HTTPException(409, "durable job input is missing")
    body = RemovalJobIn.model_validate_json(input_file.read_text())
    should_spawn = False
    with _jobs_lock:
        row = _jobs.get(canonical)
        if not row:
            raise HTTPException(404, "job not found")
        if row["status"] in {"queued", "running", "pausing", "completed"}:
            return dict(row)
        if any(
            thread.name == f"removal-{canonical}" and thread.is_alive()
            for thread in threading.enumerate()
        ):
            return dict(row)
        _cancel_events[canonical] = threading.Event()
        _stop_modes.pop(canonical, None)
        row.update(
            status="queued",
            stage="queued",
            error=None,
            detail="Queued to resume from the latest durable checkpoint",
            last_update_at=time.time(),
        )
        _persist_locked(canonical)
        should_spawn = True
    if should_spawn:
        threading.Thread(
            target=_run_job,
            args=(canonical, body),
            daemon=True,
            name=f"removal-{canonical}",
        ).start()
    return _status(canonical)


def _download(url: str, output: Path) -> None:
    if not url.startswith(("http://127.0.0.1:8000/", "http://localhost:8000/")):
        raise ValueError("source URL must be the local Anvil API")
    temp = output.with_suffix(".downloading")
    temp.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=600) as source, temp.open("wb") as target:
            shutil.copyfileobj(source, target)
        temp.replace(output)
    finally:
        temp.unlink(missing_ok=True)


def _source_is_valid(path: Path) -> bool:
    if not path.is_file():
        return False
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return False
    try:
        payload = json.loads(result.stdout)
        return (
            float(payload["format"]["duration"]) > 0
            and int(payload["streams"][0]["width"]) > 0
            and int(payload["streams"][0]["height"]) > 0
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    cancel: threading.Event | None = None,
    job_id: str | None = None,
) -> None:
    process = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    while process.poll() is None:
        if job_id is not None:
            _heartbeat(job_id)
        if cancel is not None and cancel.is_set():
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            raise RuntimeError("job cancelled")
        time.sleep(0.25)
    stdout, stderr = process.communicate()
    if process.returncode:
        raise RuntimeError(
            f"command failed ({process.returncode}): {' '.join(cmd[:5])}\n"
            f"{stderr[-2000:]}\n{stdout[-500:]}"
        )


def _decode_seed_mask(rle: dict[str, Any], width: int, height: int) -> np.ndarray:
    size = rle.get("size")
    if size != [height, width]:
        raise ValueError(
            f"SAM3 RLE size {size} does not match trusted video {[height, width]}"
        )
    counts = rle.get("counts")
    if not isinstance(counts, str) or len(counts) > width * height * 2:
        raise ValueError("SAM3 RLE counts are invalid or unreasonably large")
    encoded = dict(rle)
    encoded["counts"] = counts.encode("ascii")
    mask = mask_util.decode(encoded)
    if mask.ndim == 3:
        mask = mask[:, :, 0]
    if mask.shape != (height, width):
        raise ValueError(
            f"SAM3 mask shape {mask.shape} does not match video {(height, width)}"
        )
    return mask.astype(bool)


def _decode_manual_masks(
    data_url: str | None,
    source_width: int,
    source_height: int,
    width: int,
    height: int,
) -> list[np.ndarray]:
    """Decode a hand-painted shadow mask and split disconnected shadows."""
    if not data_url:
        return []
    prefix = "data:image/png;base64,"
    if not data_url.startswith(prefix):
        raise ValueError("manual shadow mask must be a PNG data URL")
    raw = base64.b64decode(data_url[len(prefix):], validate=True)
    if len(raw) > 3_000_000:
        raise ValueError("manual shadow mask is too large")
    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    if image.size != (source_width, source_height):
        raise ValueError(
            f"manual shadow mask size {image.size} does not match "
            f"{(source_width, source_height)}"
        )
    rgba = np.array(image)
    binary = ((rgba[:, :, 3] > 0) & (rgba[:, :, :3].max(axis=2) > 0)).astype(
        np.uint8
    )
    working = cv2.resize(binary, (width, height), interpolation=cv2.INTER_NEAREST)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        working, connectivity=8
    )
    components: list[np.ndarray] = []
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] >= 20:
            components.append(labels == label)
    return components


def _stabilize_tracked_masks(
    job_id: str,
    frames: Path,
    masks: Path,
    cancel: threading.Event,
) -> None:
    """Optical-flow-align neighboring masks and union them to eliminate flicker."""
    frame_names = sorted(frames.glob("*.png"))
    mask_names = sorted(masks.glob("*.png"))
    if len(frame_names) != len(mask_names):
        raise RuntimeError("frame/mask count mismatch during stabilization")
    stable = masks.parent / "masks-stable"
    shutil.rmtree(stable, ignore_errors=True)
    stable.mkdir()
    optical_flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)

    def gray(index: int) -> np.ndarray:
        value = cv2.imread(str(frame_names[index]), cv2.IMREAD_GRAYSCALE)
        if value is None:
            raise RuntimeError(f"could not read frame {index} for stabilization")
        return value

    def mask(index: int) -> np.ndarray:
        value = cv2.imread(str(mask_names[index]), cv2.IMREAD_GRAYSCALE)
        if value is None:
            raise RuntimeError(f"could not read mask {index} for stabilization")
        return value

    def align(neighbor: np.ndarray, current_gray: np.ndarray, neighbor_gray: np.ndarray):
        # Backward flow gives, for every current pixel, the corresponding
        # location to sample in the neighboring mask.
        flow = optical_flow.calc(current_gray, neighbor_gray, None)
        height, width = current_gray.shape
        grid_x, grid_y = np.meshgrid(
            np.arange(width, dtype=np.float32),
            np.arange(height, dtype=np.float32),
        )
        return cv2.remap(
            neighbor,
            grid_x + flow[:, :, 0],
            grid_y + flow[:, :, 1],
            cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )

    total = len(frame_names)
    close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    for index in range(total):
        if cancel.is_set():
            raise RuntimeError("job cancelled")
        current_gray = gray(index)
        candidates = [mask(index)]
        if index > 0:
            candidates.append(align(mask(index - 1), current_gray, gray(index - 1)))
        if index + 1 < total:
            candidates.append(align(mask(index + 1), current_gray, gray(index + 1)))
        # A union is intentional: under-segmentation leaves visible ghosts,
        # while optical-flow alignment avoids static motion trails.
        merged = np.maximum.reduce(candidates)
        merged = cv2.morphologyEx(merged, cv2.MORPH_CLOSE, close_kernel)
        cv2.imwrite(str(stable / f"{index:05d}.png"), merged)
        if index % 20 == 0:
            _set(
                job_id,
                stage="stabilizing",
                progress=0.42 + 0.06 * index / max(total, 1),
                detail=f"Stabilizing mask edges {index}/{total}",
            )
    shutil.rmtree(masks)
    stable.replace(masks)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload))
    temp.replace(path)


def _load_boundary(path: Path) -> list[np.ndarray]:
    if not path.is_file():
        raise RuntimeError(f"tracking checkpoint is missing: {path.name}")
    data = np.load(path, allow_pickle=False)
    values = data["masks"]
    return [values[index].astype(bool) for index in range(values.shape[0])]


def _save_boundary(path: Path, masks: list[np.ndarray]) -> None:
    temp = path.with_suffix(".tmp.npz")
    np.savez_compressed(temp, masks=np.stack(masks).astype(np.uint8))
    temp.replace(path)


def _tracking_manifest(
    root: Path,
    *,
    frame_count: int,
    seed_idx: int,
    object_count: int,
    chunk_size: int,
) -> dict[str, Any]:
    path = root / "manifest.json"
    if path.is_file():
        manifest = json.loads(path.read_text())
        expected = {
            "version": 1,
            "frame_count": frame_count,
            "seed_idx": seed_idx,
            "object_count": object_count,
        }
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise RuntimeError(
                    f"tracking checkpoint {key}={manifest.get(key)!r} "
                    f"does not match current job {value!r}"
                )
        if manifest.get("chunk_size") != chunk_size:
            # Boundaries are keyed by global cursor, so future chunks may safely
            # use a smaller memory policy after restart.
            manifest["chunk_size"] = chunk_size
            _atomic_json(path, manifest)
        return manifest
    manifest = {
        "version": 1,
        "frame_count": frame_count,
        "seed_idx": seed_idx,
        "object_count": object_count,
        "chunk_size": chunk_size,
        "forward_done_until": seed_idx,
        "reverse_done_until": seed_idx,
        "completed": False,
        "stabilized": False,
    }
    _atomic_json(path, manifest)
    return manifest


def _link_frame_chunk(
    frames: Path, chunk_root: Path, start: int, end: int
) -> Path:
    chunk_frames = chunk_root / "frames"
    shutil.rmtree(chunk_root, ignore_errors=True)
    chunk_frames.mkdir(parents=True)
    for local_index, global_index in enumerate(range(start, end + 1)):
        os.link(
            frames / f"{global_index:05d}.png",
            chunk_frames / f"{local_index:05d}.png",
        )
    return chunk_frames


def _track_chunk(
    job_id: str,
    predictor: Any,
    chunk_frames: Path,
    seed_local_idx: int,
    seed_masks: list[np.ndarray],
    reverse: bool,
    cancel: threading.Event,
) -> tuple[dict[int, np.ndarray], list[np.ndarray]]:
    """Track one bounded chunk and return union masks + far-boundary objects."""
    state = predictor.init_state(
        video_path=str(chunk_frames),
        offload_video_to_cpu=True,
        offload_state_to_cpu=True,
        async_loading_frames=True,
    )
    try:
        for object_index, seed_mask in enumerate(seed_masks, start=1):
            predictor.add_new_mask(
                state,
                frame_idx=seed_local_idx,
                obj_id=object_index,
                mask=seed_mask,
            )
        unions: dict[int, np.ndarray] = {}
        boundary: list[np.ndarray] | None = None
        boundary_index = 0 if reverse else len(list(chunk_frames.glob("*.png"))) - 1
        for local_index, _obj_ids, logits in predictor.propagate_in_video(
            state,
            start_frame_idx=seed_local_idx,
            reverse=reverse,
        ):
            _heartbeat(job_id)
            if cancel.is_set():
                raise RuntimeError("job cancelled")
            individual = (
                (logits > 0.0).squeeze(1).cpu().numpy().astype(np.uint8)
            )
            unions[local_index] = individual.any(axis=0).astype(np.uint8) * 255
            if local_index == boundary_index:
                boundary = [individual[index].astype(bool) for index in range(len(individual))]
        if boundary is None:
            raise RuntimeError("SAM2 did not produce the tracking chunk boundary")
        return unions, boundary
    finally:
        predictor.reset_state(state)
        del state
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()


def _track_masks(
    job_id: str,
    body: RemovalJobIn,
    frames: Path,
    masks: Path,
    cancel: threading.Event,
) -> None:
    from sam2.build_sam import build_sam2_video_predictor

    names = sorted(frames.glob("*.png"))
    if not names:
        raise RuntimeError("clip extraction produced no frames")
    seed_idx = min(
        len(names) - 1,
        max(0, round((body.seed_s - body.start_s) * body.fps)),
    )
    seed_masks = [
        cv2.resize(
            _decode_seed_mask(rle, body.source_width, body.source_height).astype(
                np.uint8
            ),
            (body.width, body.height),
            interpolation=cv2.INTER_NEAREST,
        ).astype(bool)
        for rle in body.mask_rles
    ]
    seed_masks.extend(
        _decode_manual_masks(
            body.manual_mask_png,
            body.source_width,
            body.source_height,
            body.width,
            body.height,
        )
    )
    if len(seed_masks) > 40:
        raise ValueError("too many subject and shadow components (max 40)")

    masks.mkdir(parents=True, exist_ok=True)
    track_root = masks.parent / "tracking-checkpoints"
    track_root.mkdir(exist_ok=True)
    configured_chunk = int(os.getenv("ANVIL_TRACK_CHUNK_FRAMES", "60"))
    configured_chunk = min(max(configured_chunk, 10), 180)
    # SAM2 keeps per-object mask state for every frame in the active chunk.
    # Bound object×frame state, so 14 identities use 30-frame chunks while a
    # single identity can use the configured 60-frame maximum.
    memory_safe_chunk = max(10, 420 // max(len(seed_masks), 1))
    chunk_size = min(configured_chunk, memory_safe_chunk)
    manifest_path = track_root / "manifest.json"
    manifest = _tracking_manifest(
        track_root,
        frame_count=len(names),
        seed_idx=seed_idx,
        object_count=len(seed_masks),
        chunk_size=chunk_size,
    )
    if manifest.get("completed") and manifest.get("stabilized"):
        return

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    predictor = build_sam2_video_predictor(
        SAM2_CONFIG, str(SAM2_CHECKPOINT), device=device
    )
    chunk_root = track_root / "active-chunk"

    def update_progress(direction: str) -> None:
        forward_done = int(manifest["forward_done_until"])
        reverse_done = int(manifest["reverse_done_until"])
        completed = (forward_done - seed_idx + 1) + (seed_idx - reverse_done)
        _set(
            job_id,
            stage="tracking",
            progress=min(0.42, 0.08 + 0.34 * completed / max(len(names), 1)),
            detail=(
                f"Checkpointed {direction} tracking: {completed}/{len(names)} frames "
                f"({len(seed_masks)} identities, {chunk_size}-frame chunks)"
            ),
        )

    # Forward from the selected frame to the end.
    while int(manifest["forward_done_until"]) < len(names) - 1:
        start = int(manifest["forward_done_until"])
        end = min(len(names) - 1, start + chunk_size - 1)
        current_seeds = (
            seed_masks
            if start == seed_idx
            else _load_boundary(track_root / f"forward-{start:05d}.npz")
        )
        chunk_frames = _link_frame_chunk(frames, chunk_root, start, end)
        unions, boundary = _track_chunk(
            job_id, predictor, chunk_frames, 0, current_seeds, False, cancel
        )
        for local_index, union in unions.items():
            cv2.imwrite(str(masks / f"{start + local_index:05d}.png"), union)
        _save_boundary(track_root / f"forward-{end:05d}.npz", boundary)
        manifest["forward_done_until"] = end
        _atomic_json(manifest_path, manifest)
        update_progress("forward")
        shutil.rmtree(chunk_root, ignore_errors=True)

    # Reverse from the selected frame to the beginning.
    while int(manifest["reverse_done_until"]) > 0:
        end = int(manifest["reverse_done_until"])
        start = max(0, end - chunk_size + 1)
        current_seeds = (
            seed_masks
            if end == seed_idx
            else _load_boundary(track_root / f"reverse-{end:05d}.npz")
        )
        chunk_frames = _link_frame_chunk(frames, chunk_root, start, end)
        seed_local = end - start
        unions, boundary = _track_chunk(
            job_id, predictor, chunk_frames, seed_local, current_seeds, True, cancel
        )
        for local_index, union in unions.items():
            cv2.imwrite(str(masks / f"{start + local_index:05d}.png"), union)
        _save_boundary(track_root / f"reverse-{start:05d}.npz", boundary)
        manifest["reverse_done_until"] = start
        _atomic_json(manifest_path, manifest)
        update_progress("reverse")
        shutil.rmtree(chunk_root, ignore_errors=True)

    manifest["completed"] = True
    _atomic_json(manifest_path, manifest)
    shutil.rmtree(chunk_root, ignore_errors=True)
    del predictor
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()

    if len(list(masks.glob("*.png"))) != len(names):
        raise RuntimeError(
            f"checkpointed tracking produced {len(list(masks.glob('*.png')))} "
            f"masks for {len(names)} frames"
        )
    _stabilize_tracked_masks(job_id, frames, masks, cancel)
    manifest["stabilized"] = True
    _atomic_json(manifest_path, manifest)


def _run_propainter_chunks(
    job_id: str,
    frames: Path,
    masks: Path,
    output: Path,
    body: RemovalJobIn,
    cancel: threading.Event,
) -> None:
    """Inpaint bounded overlapping chunks so long videos fit unified memory."""
    frame_names = sorted(frames.glob("*.png"))
    total = len(frame_names)
    if total == 0:
        raise RuntimeError("no tracked frames to inpaint")
    output.mkdir(parents=True, exist_ok=True)
    markers = output / ".chunks"
    markers.mkdir(exist_ok=True)
    chunk_size = int(
        os.getenv("ANVIL_INPAINT_CHUNK_FRAMES", str(body.fps * 2))
    )
    # ProPainter keeps every PIL mask file open while preparing a chunk. macOS
    # services commonly inherit a 256-FD soft limit, so 8-second chunks plus
    # overlap can fail around mask 253. Two-second chunks stay comfortably
    # below that limit and lower MPS memory pressure.
    chunk_size = min(max(chunk_size, 10), body.fps * 4)
    # Peak memory tracks resolution x frames-per-chunk, so a raised resolution
    # must buy fewer frames per chunk or it reproduces the measured 640x360
    # failures. This only bounds memory; it cannot make a bigger resolution
    # fast on a machine that lacks the RAM for it.
    reference_pixels = 432 * 240
    working_pixels = max(1, body.width * body.height)
    if working_pixels > reference_pixels:
        scaled = int(chunk_size * reference_pixels / working_pixels)
        chunk_size = max(10, min(chunk_size, scaled))
    overlap = max(6, body.fps // 2)
    # Overlap must not dwarf the kept region, or most compute is thrown away.
    overlap = min(overlap, max(3, chunk_size // 2))
    starts = list(range(0, total, chunk_size))

    for chunk_number, central_start in enumerate(starts):
        if cancel.is_set():
            raise RuntimeError("job cancelled")
        central_end = min(total, central_start + chunk_size)
        completed_outputs = [
            output / f"{index:05d}.png"
            for index in range(central_start, central_end)
        ]
        marker = markers / f"{chunk_number:03d}.json"
        marker_payload = {
            "chunk": chunk_number,
            "central_start": central_start,
            "central_end": central_end,
            "frame_count": central_end - central_start,
        }
        marker_valid = False
        with contextlib.suppress(OSError, ValueError, json.JSONDecodeError):
            marker_valid = json.loads(marker.read_text()) == marker_payload
        if marker_valid and all(path.is_file() for path in completed_outputs):
            _set(
                job_id,
                stage="inpainting",
                progress=0.43 + 0.39 * (chunk_number + 1) / len(starts),
                detail=(
                    f"Resumed after completed background chunk "
                    f"{chunk_number + 1}/{len(starts)}"
                ),
            )
            continue
        extended_start = max(0, central_start - overlap)
        extended_end = min(total, central_end + overlap)
        chunk_root = output.parent / f"chunk-{chunk_number:03d}"
        chunk_frames = chunk_root / "frames"
        chunk_masks = chunk_root / "masks"
        chunk_result = chunk_root / "result"
        shutil.rmtree(chunk_root, ignore_errors=True)
        chunk_frames.mkdir(parents=True)
        chunk_masks.mkdir()
        chunk_result.mkdir()
        try:
            for local, global_index in enumerate(range(extended_start, extended_end)):
                os.link(
                    frames / f"{global_index:05d}.png",
                    chunk_frames / f"{local:05d}.png",
                )
                os.link(
                    masks / f"{global_index:05d}.png",
                    chunk_masks / f"{local:05d}.png",
                )
            _run(
                [
                    sys.executable,
                    "inference_propainter.py",
                    "--video",
                    str(chunk_frames),
                    "--mask",
                    str(chunk_masks),
                    "--output",
                    str(chunk_result),
                    "--height",
                    str(body.height),
                    "--width",
                    str(body.width),
                    "--subvideo_length",
                    "50",
                    "--neighbor_length",
                    "10",
                    "--ref_stride",
                    "10",
                    "--save_fps",
                    str(body.fps),
                    "--save_frames",
                ],
                cwd=PROPAINTER,
                cancel=cancel,
                job_id=job_id,
            )
            generated = chunk_result / chunk_frames.name / "frames"
            if not generated.is_dir():
                raise RuntimeError("ProPainter did not create chunk frames")
            for global_index in range(central_start, central_end):
                local_index = global_index - extended_start
                source = generated / f"{local_index:04d}.png"
                if not source.is_file():
                    raise RuntimeError(f"missing ProPainter frame {source.name}")
                destination = output / f"{global_index:05d}.png"
                temp_destination = destination.with_suffix(".tmp")
                shutil.copyfile(source, temp_destination)
                temp_destination.replace(destination)
            _atomic_json(marker, marker_payload)
        finally:
            shutil.rmtree(chunk_root, ignore_errors=True)
        _set(
            job_id,
            stage="inpainting",
            progress=0.43 + 0.39 * (chunk_number + 1) / len(starts),
            detail=(
                f"Reconstructing background chunk {chunk_number + 1}/{len(starts)} "
                f"({central_end}/{total} frames)"
            ),
        )


def _probe_source(source: Path) -> dict[str, Any]:
    """Read geometry plus the colour tags that decide how players grade the file.

    iPhone footage is 10-bit HLG/BT.2020. Raw frames piped into ffmpeg carry no
    colour metadata, so an export built from a rawvideo pipe is tagged
    "unknown" and every player falls back to BT.709. HLG puts diffuse white at
    75% signal, so the same pixels then render visibly dim and desaturated even
    though the samples are byte-for-byte correct. Copying these tags onto the
    output is what keeps the edit looking like the original.
    """
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,pix_fmt,r_frame_rate,color_primaries,"
            "color_transfer,color_space,color_range",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    streams = json.loads(probe.stdout).get("streams") or []
    if not streams:
        raise RuntimeError("source has no video stream")
    stream = streams[0]

    def tag(name: str, fallback: str) -> str:
        value = stream.get(name)
        return value if value and value != "unknown" else fallback

    numerator, _, denominator = (stream.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = float(numerator) / float(denominator or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        fps = 0.0
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if fps <= 0 or width <= 0 or height <= 0:
        raise RuntimeError("source video metadata is invalid")

    pix_fmt = stream.get("pix_fmt") or "yuv420p"
    depth = 10 if ("10" in pix_fmt or "12" in pix_fmt) else 8
    audio = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=nw=1:nk=1",
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "depth": depth,
        "primaries": tag("color_primaries", "bt709"),
        "transfer": tag("color_transfer", "bt709"),
        "matrix": tag("color_space", "bt709"),
        "range": tag("color_range", "tv"),
        "audio_codec": audio.stdout.strip(),
    }


def _read_exact(stream: Any, size: int) -> bytes | None:
    """Read one whole frame, tolerating short reads from the decoder pipe.

    A partial frame only happens if the decoder died mid-write, which the
    non-zero exit check below reports. Dropping it is safer than padding
    garbage into the encoder.
    """
    parts: list[bytes] = []
    remaining = size
    while remaining > 0:
        block = stream.read(remaining)
        if not block:
            return None
        parts.append(block)
        remaining -= len(block)
    return b"".join(parts)


def _render_source_quality(
    job_id: str,
    source: Path,
    inpainted: Path,
    masks: Path,
    output: Path,
    body: RemovalJobIn,
    cancel: threading.Event,
) -> None:
    """Composite only the synthesized hole onto original-resolution frames."""
    info = _probe_source(source)
    width = info["width"]
    height = info["height"]
    source_fps = info["fps"]
    depth = info["depth"]
    # Escape hatch: force 8-bit H.264 if a player cannot handle HEVC Main 10.
    # The colour tags below still apply, so the export stays correctly graded.
    depth_override = os.getenv("ANVIL_OUTPUT_DEPTH", "auto").strip().lower()
    if depth_override == "8":
        depth = 8
    elif depth_override == "10":
        depth = 10
    # 10-bit sources stay 10-bit end to end. Flattening HLG to 8 bits banded the
    # sky badly, and the extra depth costs only pipe bandwidth.
    pipe_format = "rgb48le" if depth == 10 else "rgb24"
    sample_dtype = np.uint16 if depth == 10 else np.uint8
    peak = 65535.0 if depth == 10 else 255.0
    frame_bytes = width * height * 3 * (2 if depth == 10 else 1)
    duration = body.end_s - body.start_s
    total_output = max(1, round(duration * source_fps))
    total_work = len(list(inpainted.glob("*.png")))
    if total_work == 0:
        raise RuntimeError("ProPainter produced no frames")

    # OpenCV cannot express 10-bit output and drops colour metadata, so decode
    # through ffmpeg instead and keep the pipeline colour-managed.
    decode_command = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-ss",
        f"{body.start_s:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-f",
        "rawvideo",
        "-pix_fmt",
        pipe_format,
        "pipe:1",
    ]
    command = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        pipe_format,
        "-s",
        f"{width}x{height}",
        "-r",
        f"{source_fps:.8f}",
        "-i",
        "pipe:0",
        "-ss",
        f"{body.start_s:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0?",
    ]
    # ffmpeg auto-inserts an RGB->YUV scaler for the rawvideo pipe, and that
    # filter resets primaries/transfer to "unspecified" on the frames it emits,
    # which silently overrides the -color_* container options. Writing the VUI
    # through the encoder's own parameters is the only way the HLG transfer
    # survives, and it is exactly the tag that keeps the file from looking dim.
    vui = (
        f"colorprim={info['primaries']}"
        f":transfer={info['transfer']}"
        f":colormatrix={info['matrix']}"
        f":range={'full' if info['range'] == 'pc' else 'limited'}"
    )
    if depth == 10:
        command += [
            "-c:v",
            "libx265",
            "-preset",
            os.getenv("ANVIL_ENCODE_PRESET", "medium"),
            "-crf",
            os.getenv("ANVIL_ENCODE_CRF", "16"),
            "-pix_fmt",
            "yuv420p10le",
            "-x265-params",
            vui,
            # Apple players require the hvc1 brand; hev1 shows a black frame.
            "-tag:v",
            "hvc1",
        ]
    else:
        command += [
            "-c:v",
            "libx264",
            "-preset",
            os.getenv("ANVIL_ENCODE_PRESET", "slow"),
            "-crf",
            os.getenv("ANVIL_ENCODE_CRF", "16"),
            "-pix_fmt",
            "yuv420p",
            "-x264-params",
            vui,
        ]
    command += [
        "-color_primaries",
        info["primaries"],
        "-color_trc",
        info["transfer"],
        "-colorspace",
        info["matrix"],
        "-color_range",
        info["range"],
    ]
    # Keep the original audio untouched whenever the codec already fits MP4.
    if info["audio_codec"] in {"aac", "mp3"}:
        command += ["-c:a", "copy"]
    else:
        command += ["-c:a", "aac", "-b:a", "256k"]
    command += ["-movflags", "+faststart", str(output)]

    decoder_log = output.with_suffix(".decode.log")
    decoder_errors = decoder_log.open("wb")
    decoder = subprocess.Popen(
        decode_command,
        stdout=subprocess.PIPE,
        stderr=decoder_errors,
        bufsize=frame_bytes,
    )
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    cache: OrderedDict[int, tuple[np.ndarray, np.ndarray]] = OrderedDict()

    def working(index: int) -> tuple[np.ndarray, np.ndarray]:
        index = min(max(index, 0), total_work - 1)
        cached = cache.get(index)
        if cached is not None:
            cache.move_to_end(index)
            return cached
        background = cv2.imread(str(inpainted / f"{index:05d}.png"))
        mask = cv2.imread(str(masks / f"{index:05d}.png"), cv2.IMREAD_GRAYSCALE)
        if background is None or mask is None:
            raise RuntimeError(f"missing composite inputs for frame {index}")
        # The decoder pipe delivers RGB, so flip ProPainter's BGR read once here
        # instead of per composited frame.
        background = np.ascontiguousarray(background[:, :, ::-1])
        # Match ProPainter's four-pixel working-mask dilation so every original
        # subject pixel is replaced. Keep the entire core at alpha=1; feather
        # only outside it. Blurring the core itself caused translucent ghosts.
        cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        core = cv2.dilate(mask, cross, iterations=4)
        outer_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        outer = cv2.dilate(core, outer_kernel, iterations=1)
        feather = (
            cv2.GaussianBlur(outer, (0, 0), sigmaX=2.5).astype(np.float32) / 255.0
        )
        alpha = np.where(core > 0, 1.0, feather).astype(np.float32)
        cache[index] = (background, alpha)
        while len(cache) > 4:
            cache.popitem(last=False)
        return background, alpha

    try:
        assert process.stdin is not None
        assert decoder.stdout is not None
        written = 0
        while True:
            _heartbeat(job_id)
            if cancel.is_set():
                raise RuntimeError("job cancelled")
            payload = _read_exact(decoder.stdout, frame_bytes)
            if payload is None:
                break
            output_index = written
            original = np.frombuffer(payload, dtype=sample_dtype).reshape(
                height, width, 3
            )
            position = output_index / source_fps * body.fps
            low = min(int(position), total_work - 1)
            high = min(low + 1, total_work - 1)
            fraction = position - int(position)
            bg0, alpha0 = working(low)
            bg1, alpha1 = working(high)
            background = cv2.addWeighted(bg0, 1.0 - fraction, bg1, fraction, 0)
            alpha = alpha0 * (1.0 - fraction) + alpha1 * fraction
            background = cv2.resize(
                background, (width, height), interpolation=cv2.INTER_LANCZOS4
            )
            alpha = cv2.resize(alpha, (width, height), interpolation=cv2.INTER_LINEAR)
            alpha3 = alpha[:, :, None]
            # Composite in float32 and lift the 8-bit reconstruction to the
            # source's range so the patch matches its surroundings exactly.
            reconstructed = background.astype(np.float32)
            reconstructed *= peak / 255.0
            reconstructed *= alpha3
            composite = original.astype(np.float32)
            composite *= 1.0 - alpha3
            composite += reconstructed
            np.clip(composite, 0.0, peak, out=composite)
            process.stdin.write(composite.astype(sample_dtype).tobytes())
            written += 1
            if output_index % max(1, round(source_fps * 2)) == 0:
                _set(
                    job_id,
                    stage="encoding",
                    progress=0.83 + 0.16 * min(1.0, output_index / total_output),
                    detail=(
                        "Compositing reconstructed pixels onto source-quality video "
                        f"{output_index}/{total_output}"
                    ),
                )
        if decoder.wait() != 0:
            decoder_errors.flush()
            reason = decoder_log.read_text(errors="replace")[-2000:]
            raise RuntimeError(f"source decode failed during final composite: {reason}")
        process.stdin.close()
        stderr = process.stderr.read() if process.stderr is not None else b""
        return_code = process.wait()
        if return_code:
            raise RuntimeError(
                f"high-quality encoding failed ({return_code}): "
                f"{stderr.decode(errors='replace')[-2000:]}"
            )
        # ffmpeg's trimmed decode can legitimately land a frame or two either
        # side of the nominal duration, matching the extraction tolerance.
        if abs(written - total_output) > 2 or written == 0:
            raise RuntimeError(f"encoded {written}/{total_output} expected frames")
    except Exception:
        if process.poll() is None:
            process.terminate()
        raise
    finally:
        if decoder.poll() is None:
            decoder.terminate()
        with contextlib.suppress(Exception):
            if decoder.stdout is not None:
                decoder.stdout.close()
        decoder_errors.close()
        decoder_log.unlink(missing_ok=True)


def _run_job(job_id: str, body: RemovalJobIn) -> None:
    job = JOBS_ROOT / job_id
    source = job / "source.mp4"
    frames = job / "frames"
    masks = job / "masks"
    inpainted = job / "inpainted"
    cancel = _cancel_events.setdefault(job_id, threading.Event())
    completed_successfully = False
    try:
        with _job_gate:
            if cancel.is_set():
                return
            _start_attempt(job_id)
            job.mkdir(parents=True, exist_ok=True)
            (job / "removed.mp4").unlink(missing_ok=True)
            for stale_chunk in job.glob("chunk-*"):
                shutil.rmtree(stale_chunk, ignore_errors=True)
            shutil.rmtree(job / "frames.extracting", ignore_errors=True)
            masks.mkdir(exist_ok=True)
            inpainted.mkdir(exist_ok=True)
            if not _source_is_valid(source):
                source.unlink(missing_ok=True)
                _set(job_id, status="running", stage="downloading", progress=0.02)
                _download(body.source_url, source)

            duration = body.end_s - body.start_s
            expected_frames = max(1, round(duration * body.fps))
            existing_names = sorted(frames.glob("*.png")) if frames.is_dir() else []
            existing_frames = len(existing_names)
            contiguous = all(
                path.stem == f"{index:05d}"
                for index, path in enumerate(existing_names)
            )
            extraction_manifest = frames / "extraction.json"
            extraction_meta: dict[str, Any] = {}
            with contextlib.suppress(OSError, ValueError, json.JSONDecodeError):
                extraction_meta = json.loads(extraction_manifest.read_text())
            # Never infer extraction completion from file count alone. The
            # manifest is written only after ffmpeg exits successfully.
            extraction_valid = (
                existing_frames > 0
                and contiguous
                and abs(existing_frames - expected_frames) <= 2
                and extraction_meta
                == {
                    "start_s": body.start_s,
                    "end_s": body.end_s,
                    "fps": body.fps,
                    "width": body.width,
                    "height": body.height,
                    "frame_count": existing_frames,
                    "format": "png",
                }
            )
            if not extraction_valid:
                shutil.rmtree(frames, ignore_errors=True)
                shutil.rmtree(masks, ignore_errors=True)
                shutil.rmtree(inpainted, ignore_errors=True)
                shutil.rmtree(job / "tracking-checkpoints", ignore_errors=True)
                temp_frames = job / "frames.extracting"
                shutil.rmtree(temp_frames, ignore_errors=True)
                temp_frames.mkdir()
                masks.mkdir()
                inpainted.mkdir()
                _set(job_id, status="running", stage="extracting", progress=0.04)
                _run(
                    [
                        "ffmpeg",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        f"{body.start_s:.3f}",
                        "-t",
                        f"{duration:.3f}",
                        "-i",
                        str(source),
                        "-vf",
                        f"fps={body.fps},scale={body.width}:{body.height}:flags=lanczos",
                        "-start_number",
                        "0",
                        # Lossless frames. JPEG cost roughly 2x the pixel error
                        # and, because ffmpeg writes BT.2020 chroma that OpenCV
                        # then decodes as BT.601, gave the reconstructed patch a
                        # faint green cast against untouched surroundings.
                        "-compression_level",
                        "1",
                        str(temp_frames / "%05d.png"),
                    ],
                    cancel=cancel,
                    job_id=job_id,
                )
                extracted = len(list(temp_frames.glob("*.png")))
                if extracted <= 0 or abs(extracted - expected_frames) > 2:
                    raise RuntimeError(
                        f"ffmpeg extracted {extracted} frames; expected about "
                        f"{expected_frames}"
                    )
                _atomic_json(
                    temp_frames / "extraction.json",
                    {
                        "start_s": body.start_s,
                        "end_s": body.end_s,
                        "fps": body.fps,
                        "width": body.width,
                        "height": body.height,
                        "frame_count": extracted,
                        "format": "png",
                    },
                )
                temp_frames.replace(frames)
                existing_frames = extracted
            else:
                _set(
                    job_id,
                    status="running",
                    stage="tracking",
                    detail=(
                        f"Resuming with {existing_frames} existing frames; "
                        "loading tracking checkpoint"
                    ),
                )

            _set(job_id, stage="tracking", progress=0.08)
            _track_masks(job_id, body, frames, masks, cancel)

            _set(
                job_id,
                stage="inpainting",
                progress=0.45,
                detail="ProPainter is reconstructing the hidden background",
            )
            _run_propainter_chunks(job_id, frames, masks, inpainted, body, cancel)

            _set(
                job_id,
                stage="encoding",
                progress=0.92,
                detail="Restoring original audio and encoding MP4",
            )
            _render_source_quality(
                job_id,
                source,
                inpainted,
                masks,
                job / "removed.mp4",
                body,
                cancel,
            )
            _set(
                job_id,
                status="completed",
                stage="completed",
                progress=1.0,
                detail="Object removed",
                output_ready=True,
                completed_at=time.time(),
            )
            _finish_attempt(job_id)
            completed_successfully = True
    except Exception as exc:
        (job / "removed.mp4").unlink(missing_ok=True)
        if not cancel.is_set():
            _finish_attempt(job_id)
            # Preserve every completed stage/chunk. A retry with the same job
            # UUID resumes from manifests and existing frame outputs.
            _set(
                job_id,
                status="interrupted",
                stage="interrupted",
                error=f"{type(exc).__name__}: {exc}"[:4000],
                detail="Interrupted; restart the worker to resume from the latest checkpoint",
                traceback=traceback.format_exc()[-8000:],
            )
    finally:
        # Keep checkpoints and stage artifacts after interruption/failure so a
        # later worker can resume. They become disposable only after a durable
        # output exists or the user explicitly cancels.
        if completed_successfully:
            for path in (
                source,
                frames,
                masks,
                inpainted,
                job / "tracking-checkpoints",
                job / "frames.extracting",
            ):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
        mode = _stop_modes.get(job_id)
        if cancel.is_set() and mode == "cancel":
            shutil.rmtree(job, ignore_errors=True)
            with _jobs_lock:
                _cancel_events.pop(job_id, None)
                _stop_modes.pop(job_id, None)
        elif cancel.is_set() and mode == "pause":
            with _jobs_lock:
                _cancel_events[job_id] = threading.Event()
                _stop_modes.pop(job_id, None)
            _finish_attempt(job_id)
            _set(
                job_id,
                status="interrupted",
                stage="paused",
                detail="Paused safely; completed chunks are preserved",
                error=None,
            )


if __name__ == "__main__":
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    for status_file in JOBS_ROOT.glob("*/status.json"):
        with contextlib.suppress(OSError, ValueError, json.JSONDecodeError):
            row = json.loads(status_file.read_text())
            job_id = str(row["id"])
            _jobs[job_id] = row
            _cancel_events[job_id] = threading.Event()
            if row.get("status") in {"queued", "running", "pausing"}:
                _freeze_crashed_attempt(job_id)
                _set(
                    job_id,
                    status="interrupted",
                    stage="interrupted",
                    detail=(
                        "Worker restarted; completed chunks are safe. "
                        "Resume explicitly from the UI."
                    ),
                    error=None,
                )
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.getenv("ANVIL_PERSONAL_WORKER_PORT", "8002")),
        log_level="info",
    )
