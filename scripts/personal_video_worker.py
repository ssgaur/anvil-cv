#!/usr/bin/env python3
"""Native-Mac personal worker for SAM2 tracking + ProPainter removal.

Run outside Docker so PyTorch can use Apple MPS:

    python3 scripts/personal_video_worker.py

This backend is intentionally optional and non-commercial. ProPainter source
and weights must be installed with `install_propainter_personal.py`; nothing
from ProPainter is redistributed with Anvil.
"""

from __future__ import annotations

import contextlib
import gc
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
JOBS_ROOT = PERSONAL / "jobs"
SAM2_CHECKPOINT = ROOT / "checkpoints" / "sam2.1_hiera_base_plus.pt"
SAM2_CONFIG = "configs/sam2.1/sam2.1_hiera_b+.yaml"
TOKEN = os.getenv("ANVIL_PERSONAL_WORKER_TOKEN", "anvil-personal-worker")

app = FastAPI(title="Anvil personal video worker")
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_job_gate = threading.Semaphore(1)
_cancel_events: dict[str, threading.Event] = {}


class RemovalJobIn(BaseModel):
    job_id: str
    source_url: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    seed_s: float = Field(ge=0)
    mask_rles: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    source_width: int = Field(gt=0)
    source_height: int = Field(gt=0)
    fps: int = Field(default=15, ge=8, le=15)
    width: int = Field(default=432, ge=320, le=640)
    height: int = Field(default=240, ge=176, le=368)


def require_token(x_anvil_personal: str | None = Header(default=None)) -> None:
    import secrets

    if not x_anvil_personal or not secrets.compare_digest(x_anvil_personal, TOKEN):
        raise HTTPException(403, "personal worker credentials required")


def _set(job_id: str, **values: Any) -> None:
    with _jobs_lock:
        event = _cancel_events.get(job_id)
        if event is not None and event.is_set():
            return
        _jobs.setdefault(job_id, {}).update(values)
        job_dir = JOBS_ROOT / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        temp = job_dir / "status.json.tmp"
        temp.write_text(json.dumps(_jobs[job_id]))
        temp.replace(job_dir / "status.json")


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
        return dict(row)


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
    }


@app.post("/jobs", dependencies=[Depends(require_token)])
def create_job(body: RemovalJobIn) -> dict[str, Any]:
    installed, error = _check_install()
    if not installed:
        raise HTTPException(503, error)
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


def _download(url: str, output: Path) -> None:
    if not url.startswith(("http://127.0.0.1:8000/", "http://localhost:8000/")):
        raise ValueError("source URL must be the local Anvil API")
    with urllib.request.urlopen(url, timeout=600) as source, output.open("wb") as target:
        shutil.copyfileobj(source, target)


def _run(
    cmd: list[str], *, cwd: Path | None = None, cancel: threading.Event | None = None
) -> None:
    process = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    while process.poll() is None:
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


def _track_masks(
    job_id: str,
    body: RemovalJobIn,
    frames: Path,
    masks: Path,
    cancel: threading.Event,
) -> None:
    from sam2.build_sam import build_sam2_video_predictor

    names = sorted(frames.glob("*.jpg"))
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

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    predictor = build_sam2_video_predictor(
        SAM2_CONFIG, str(SAM2_CHECKPOINT), device=device
    )
    state = predictor.init_state(
        video_path=str(frames),
        offload_video_to_cpu=True,
        offload_state_to_cpu=True,
        async_loading_frames=True,
    )
    for object_index, seed_mask in enumerate(seed_masks, start=1):
        predictor.add_new_mask(
            state,
            frame_idx=seed_idx,
            obj_id=object_index,
            mask=seed_mask,
        )
    tracked: dict[int, np.ndarray] = {}

    directions = ((False, seed_idx), (True, seed_idx))
    completed = 0
    expected = len(names) + seed_idx
    for reverse, start in directions:
        for frame_idx, _obj_ids, logits in predictor.propagate_in_video(
            state, start_frame_idx=start, reverse=reverse
        ):
            if cancel.is_set():
                raise RuntimeError("job cancelled")
            tracked[frame_idx] = (
                (logits > 0.0)
                .any(dim=0)
                .squeeze()
                .cpu()
                .numpy()
                .astype(np.uint8)
                * 255
            )
            completed += 1
            _set(
                job_id,
                stage="tracking",
                progress=min(0.42, 0.08 + 0.34 * completed / max(expected, 1)),
                detail=(
                    f"Tracking {len(seed_masks)} object(s): "
                    f"{len(tracked)}/{len(names)} frames"
                ),
            )

    masks.mkdir(parents=True, exist_ok=True)
    for index in range(len(names)):
        mask = tracked.get(index)
        if mask is None:
            nearest = min(tracked, key=lambda value: abs(value - index))
            mask = tracked[nearest]
        Image.fromarray(mask).save(masks / f"{index:05d}.png")

    predictor.reset_state(state)
    del state, predictor
    gc.collect()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()


def _run_propainter_chunks(
    job_id: str,
    frames: Path,
    masks: Path,
    output: Path,
    body: RemovalJobIn,
    cancel: threading.Event,
) -> None:
    """Inpaint bounded overlapping chunks so long videos fit unified memory."""
    frame_names = sorted(frames.glob("*.jpg"))
    total = len(frame_names)
    if total == 0:
        raise RuntimeError("no tracked frames to inpaint")
    output.mkdir(parents=True, exist_ok=True)
    chunk_size = body.fps * 8
    overlap = max(6, body.fps // 2)
    starts = list(range(0, total, chunk_size))

    for chunk_number, central_start in enumerate(starts):
        if cancel.is_set():
            raise RuntimeError("job cancelled")
        central_end = min(total, central_start + chunk_size)
        extended_start = max(0, central_start - overlap)
        extended_end = min(total, central_end + overlap)
        chunk_root = output.parent / f"chunk-{chunk_number:03d}"
        chunk_frames = chunk_root / "frames"
        chunk_masks = chunk_root / "masks"
        chunk_result = chunk_root / "result"
        chunk_frames.mkdir(parents=True)
        chunk_masks.mkdir()
        chunk_result.mkdir()
        try:
            for local, global_index in enumerate(range(extended_start, extended_end)):
                os.link(
                    frames / f"{global_index:05d}.jpg",
                    chunk_frames / f"{local:05d}.jpg",
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
            )
            generated = chunk_result / chunk_frames.name / "frames"
            if not generated.is_dir():
                raise RuntimeError("ProPainter did not create chunk frames")
            for global_index in range(central_start, central_end):
                local_index = global_index - extended_start
                source = generated / f"{local_index:04d}.png"
                if not source.is_file():
                    raise RuntimeError(f"missing ProPainter frame {source.name}")
                shutil.copyfile(source, output / f"{global_index:05d}.png")
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
    capture = cv2.VideoCapture(str(source))
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if source_fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise RuntimeError("source video metadata is invalid")
    capture.set(cv2.CAP_PROP_POS_MSEC, body.start_s * 1000.0)
    duration = body.end_s - body.start_s
    total_output = max(1, round(duration * source_fps))
    total_work = len(list(inpainted.glob("*.png")))
    if total_work == 0:
        capture.release()
        raise RuntimeError("ProPainter produced no frames")

    command = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
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
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]
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
        kernel = np.ones((7, 7), np.uint8)
        alpha = cv2.dilate(mask, kernel, iterations=1)
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=2.5).astype(np.float32) / 255.0
        cache[index] = (background, alpha)
        while len(cache) > 4:
            cache.popitem(last=False)
        return background, alpha

    try:
        assert process.stdin is not None
        written = 0
        for output_index in range(total_output):
            if cancel.is_set():
                raise RuntimeError("job cancelled")
            ok, original = capture.read()
            if not ok or original is None:
                raise RuntimeError(
                    f"source decode ended at frame {output_index}/{total_output}"
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
            composite = (
                original.astype(np.float32) * (1.0 - alpha3)
                + background.astype(np.float32) * alpha3
            ).clip(0, 255).astype(np.uint8)
            process.stdin.write(composite.tobytes())
            written += 1
            if output_index % max(1, round(source_fps * 2)) == 0:
                _set(
                    job_id,
                    stage="encoding",
                    progress=0.83 + 0.16 * output_index / total_output,
                    detail=(
                        "Compositing reconstructed pixels onto source-quality video "
                        f"{output_index}/{total_output}"
                    ),
                )
        process.stdin.close()
        stderr = process.stderr.read() if process.stderr is not None else b""
        return_code = process.wait()
        if return_code:
            raise RuntimeError(
                f"high-quality encoding failed ({return_code}): "
                f"{stderr.decode(errors='replace')[-2000:]}"
            )
        if written != total_output:
            raise RuntimeError(f"encoded {written}/{total_output} expected frames")
    except Exception:
        if process.poll() is None:
            process.terminate()
        raise
    finally:
        capture.release()


def _run_job(job_id: str, body: RemovalJobIn) -> None:
    job = JOBS_ROOT / job_id
    source = job / "source.mp4"
    frames = job / "frames"
    masks = job / "masks"
    inpainted = job / "inpainted"
    cancel = _cancel_events.setdefault(job_id, threading.Event())
    try:
        with _job_gate:
            if cancel.is_set():
                return
            for path in (source, frames, masks, inpainted, job / "removed.mp4"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            for stale_chunk in job.glob("chunk-*"):
                shutil.rmtree(stale_chunk, ignore_errors=True)
            frames.mkdir(parents=True)
            masks.mkdir()
            inpainted.mkdir()
            _set(job_id, status="running", stage="downloading", progress=0.02)
            _download(body.source_url, source)

            duration = body.end_s - body.start_s
            _set(job_id, stage="extracting", progress=0.04)
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
                    f"fps={body.fps},scale={body.width}:{body.height}",
                    "-start_number",
                    "0",
                    "-q:v",
                    "2",
                    str(frames / "%05d.jpg"),
                ],
                cancel=cancel,
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
    except Exception as exc:
        (job / "removed.mp4").unlink(missing_ok=True)
        if not cancel.is_set():
            _set(
                job_id,
                status="failed",
                stage="failed",
                error=f"{type(exc).__name__}: {exc}"[:4000],
                traceback=traceback.format_exc()[-8000:],
            )
    finally:
        # Preserve only input/status and a successful output. Full source
        # copies, frames, masks, and model intermediates are always disposable.
        for path in (source, frames, masks, inpainted):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        if cancel.is_set():
            shutil.rmtree(job, ignore_errors=True)
            with _jobs_lock:
                _cancel_events.pop(job_id, None)


if __name__ == "__main__":
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    for status_file in JOBS_ROOT.glob("*/status.json"):
        with contextlib.suppress(OSError, ValueError, json.JSONDecodeError):
            row = json.loads(status_file.read_text())
            job_id = str(row["id"])
            _jobs[job_id] = row
            _cancel_events[job_id] = threading.Event()
            if row.get("status") in {"queued", "running"}:
                input_file = status_file.parent / "input.json"
                if input_file.is_file():
                    body = RemovalJobIn.model_validate_json(input_file.read_text())
                    threading.Thread(
                        target=_run_job,
                        args=(job_id, body),
                        daemon=True,
                        name=f"removal-{job_id}",
                    ).start()
                else:
                    _set(
                        job_id,
                        status="failed",
                        stage="failed",
                        error="worker restarted but durable job input is missing",
                    )
    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="info")
