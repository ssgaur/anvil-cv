"""SAM3 frame discovery and browser-video preparation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import cv2

from anvil_ml.providers import get_provider

_SHOWCASE_ROOT = Path("/data/showcase").resolve()


def _video_dir(video_id: str) -> Path:
    return _SHOWCASE_ROOT / "videos" / str(UUID(video_id))


def _source_video(video_dir: Path) -> Path:
    sources = sorted(video_dir.glob("source.*"))
    if len(sources) != 1:
        raise FileNotFoundError(f"expected one source video, found {len(sources)}")
    return sources[0]


def showcase_ready() -> dict[str, Any]:
    """Validate all cached SAM3 processor/model artifacts without loading weights."""
    from huggingface_hub import try_to_load_from_cache
    from transformers import Sam3Processor

    model_id = os.getenv("SAM3_MODEL_ID", "facebook/sam3")
    model_cached = isinstance(
        try_to_load_from_cache(model_id, "model.safetensors"), str
    )
    processor_ready = False
    try:
        Sam3Processor.from_pretrained(model_id, local_files_only=True)
        processor_ready = True
    except (OSError, ValueError):
        pass
    return {
        "sam3": model_cached and processor_ready,
        "artifacts": {
            "model.safetensors": model_cached,
            "processor_and_tokenizer": processor_ready,
        },
        "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
    }


def _probe(path: Path, *, include_codec: bool) -> dict[str, Any]:
    stream_fields = "width,height,avg_frame_rate,nb_frames"
    if include_codec:
        stream_fields = "codec_name," + stream_fields
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            f"stream={stream_fields}:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def prepare_video(video_id: str) -> dict[str, Any]:
    """Preserve the uploaded source and create a separate seekable H.264 playback."""
    video_dir = _video_dir(video_id)
    source = _source_video(video_dir)
    playback = video_dir / "playback.mp4"
    poster = video_dir / "poster.jpg"
    source_meta = _probe(source, include_codec=True)
    source_stream = source_meta["streams"][0]
    source_duration = float(source_meta["format"]["duration"])

    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(playback),
        ],
        check=True,
        capture_output=True,
    )
    # Probe playback because ffmpeg auto-rotates phone videos; all masks and UI
    # coordinates must use the post-rotation dimensions.
    playback_meta = _probe(playback, include_codec=False)
    stream = playback_meta["streams"][0]
    duration = float(playback_meta["format"]["duration"])
    rate = str(stream.get("avg_frame_rate") or "0/1")
    numerator, denominator = (float(value) for value in rate.split("/", 1))
    fps = numerator / denominator if denominator else 0.0
    frame_count = int(stream.get("nb_frames") or round(duration * fps))

    poster_at = min(max(source_duration * 0.08, 0.5), 5.0)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-ss",
            f"{poster_at:.3f}",
            "-i",
            str(playback),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(1280,iw)':-2",
            "-q:v",
            "2",
            str(poster),
        ],
        check=True,
        capture_output=True,
    )
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": round(fps, 3),
        "duration_s": round(duration, 3),
        "frame_count": frame_count,
        "codec": str(source_stream.get("codec_name") or "unknown"),
    }


def _read_frame(capture: cv2.VideoCapture, timestamp_s: float):
    capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_s * 1000.0)
    ok, frame = capture.read()
    if not ok or frame is None:
        raise RuntimeError(f"could not decode frame at {timestamp_s:.2f}s")
    return frame


def discover_frame(
    video_id: str,
    timestamp_s: float,
    concept: str,
    threshold: float,
) -> dict[str, Any]:
    """Run SAM3 on one video moment and return independently clickable masks."""
    video = _video_dir(video_id) / "playback.mp4"
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError("could not open showcase video")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps if fps else 0.0
    timestamp = min(max(timestamp_s, 0.0), max(duration - 0.05, 0.0))
    frame = _read_frame(capture, timestamp)
    capture.release()
    height, width = frame.shape[:2]

    work = _video_dir(video_id) / f"discover-{time.time_ns()}.jpg"
    cv2.imwrite(str(work), frame)
    try:
        instances = get_provider("sam3").predict_concept(
            str(work), concept.strip(), threshold=threshold, max_instances=100
        )
    finally:
        work.unlink(missing_ok=True)
    return {
        "timestamp_s": round(timestamp, 3),
        "concept": concept.strip(),
        "width": width,
        "height": height,
        "instances": [instance.to_dict() for instance in instances],
    }
