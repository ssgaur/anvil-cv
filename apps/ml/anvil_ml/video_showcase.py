"""Local SAM3 + Ollama visual question answering for one video.

The video is sampled rather than propagated frame-by-frame. Full SAM3 video
tracking over a two-minute 30fps clip would require processing 3,600+ frames
and take many hours on CPU. Five strategically sampled frames give a useful
visual answer in under a minute while still using the real SAM3 model to ground
the chosen evidence frame with masks and counts.
"""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import cv2
import httpx
import numpy as np

from anvil_ml.providers import get_provider

log = logging.getLogger("anvil.ml.video_showcase")

_SHOWCASE_ROOT = Path("/data/showcase").resolve()
_PALETTE = (
    (34, 197, 94),
    (59, 130, 246),
    (244, 63, 94),
    (234, 179, 8),
    (168, 85, 247),
    (20, 184, 166),
)


def _video_dir(video_id: str) -> Path:
    canonical = str(UUID(video_id))
    return _SHOWCASE_ROOT / "videos" / canonical


def _source_video(video_dir: Path) -> Path:
    sources = sorted(video_dir.glob("source.*"))
    if len(sources) != 1:
        raise FileNotFoundError(f"expected one source video, found {len(sources)}")
    return sources[0]


def showcase_ready() -> dict[str, Any]:
    """Cheap readiness check: required cached artifacts + ffmpeg binaries."""
    from huggingface_hub import try_to_load_from_cache
    from transformers import Sam3Processor

    model_id = os.getenv("SAM3_MODEL_ID", "facebook/sam3")
    model_cached = isinstance(
        try_to_load_from_cache(model_id, "model.safetensors"), str
    )
    processor_ready = False
    try:
        # This validates tokenizer.json, tokenizer config, vocabulary/merges,
        # processor config and all cross-file references without loading the
        # 3.2GB model or touching the network.
        Sam3Processor.from_pretrained(model_id, local_files_only=True)
        processor_ready = True
    except (OSError, ValueError):
        pass
    ollama_url: str | None = None
    ollama_models: list[str] = []
    with contextlib.suppress(RuntimeError):
        ollama_url, ollama_models = resolve_ollama_url()
    ollama_model = os.getenv("OLLAMA_VISION_MODEL", "gemma3:4b")
    return {
        "sam3": model_cached and processor_ready,
        "artifacts": {
            "model.safetensors": model_cached,
            "processor_and_tokenizer": processor_ready,
        },
        "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
        "ollama": ollama_model in ollama_models,
        "ollama_model": ollama_model,
        "ollama_models": ollama_models,
        "ollama_url": ollama_url,
    }


def resolve_ollama_url() -> tuple[str, list[str]]:
    """Find the host Ollama service across Docker/Rancher/Linux layouts."""
    configured = os.getenv("OLLAMA_URL", "auto").rstrip("/")
    candidates = (
        [configured]
        if configured and configured != "auto"
        else [
            "http://host.docker.internal:11434",
            "http://host.rancher-desktop.internal:11434",
        ]
    )
    for candidate in candidates:
        try:
            response = httpx.get(f"{candidate}/api/tags", timeout=2.5)
            response.raise_for_status()
            models = [row["name"] for row in response.json().get("models", [])]
            return candidate, models
        except (httpx.HTTPError, KeyError, TypeError):
            continue
    raise RuntimeError(
        "Ollama is unreachable from the ML container. Start `ollama serve`; "
        "on Linux expose Ollama on the host gateway."
    )


def prepare_video(video_id: str) -> dict[str, Any]:
    """Probe a video and make an H.264 MP4 browsers can reliably stream."""
    video_dir = _video_dir(video_id)
    source = _source_video(video_dir)
    playback = video_dir / "playback.mp4"
    poster = video_dir / "poster.jpg"
    if not source.is_file():
        raise FileNotFoundError(source)
    playback.parent.mkdir(parents=True, exist_ok=True)
    poster.parent.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,avg_frame_rate,nb_frames:format=duration,size",
            "-of",
            "json",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(probe.stdout)
    stream = metadata["streams"][0]
    duration = float(metadata["format"]["duration"])
    rate = str(stream.get("avg_frame_rate") or "0/1")
    num, den = (float(x) for x in rate.split("/", 1))
    fps = num / den if den else 0.0
    frame_count = int(stream.get("nb_frames") or round(duration * fps))

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
    poster_at = min(max(duration * 0.08, 0.5), 5.0)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-ss",
            f"{poster_at:.3f}",
            "-i",
            str(source),
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
        "codec": str(stream.get("codec_name") or "unknown"),
    }


def _sample_timestamps(duration: float, current_time: float | None) -> list[float]:
    wide = [duration * x for x in (0.08, 0.28, 0.5, 0.72, 0.92)]
    if current_time is not None:
        current = max(0.0, min(current_time, duration))
        wide[2] = current
    # Rounded de-duplication matters for very short clips.
    return sorted({round(max(0.0, min(t, max(duration - 0.05, 0.0))), 3) for t in wide})


def _read_frame(cap: cv2.VideoCapture, timestamp_s: float) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_s * 1000.0)
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError(f"could not decode frame at {timestamp_s:.2f}s")
    return frame


def _reasoning_frame(frame: np.ndarray) -> np.ndarray:
    """Bound retained frame memory; the evidence frame is re-read at full size."""
    h, w = frame.shape[:2]
    scale = min(1.0, 1280.0 / max(h, w))
    if scale == 1.0:
        return frame
    return cv2.resize(frame, (round(w * scale), round(h * scale)))


def _vision_jpeg(frame: np.ndarray) -> str:
    h, w = frame.shape[:2]
    scale = min(1.0, 896.0 / max(h, w))
    if scale < 1.0:
        frame = cv2.resize(frame, (round(w * scale), round(h * scale)))
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
    if not ok:
        raise RuntimeError("failed to encode video frame")
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _ask_ollama(
    question: str,
    frames: list[np.ndarray],
    timestamps: list[float],
    ollama_url: str,
    ollama_model: str,
) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "caveat": {"type": "string"},
            "grounding_concept": {"type": "string"},
            "evidence_frame_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": len(frames) - 1,
            },
        },
        "required": [
            "answer",
            "confidence",
            "caveat",
            "grounding_concept",
            "evidence_frame_index",
        ],
    }
    timeline = ", ".join(f"frame {i}={t:.1f}s" for i, t in enumerate(timestamps))
    system = (
        "You answer English questions about a video using sampled visual frames. "
        "Never claim to hear audio; no audio transcript is available. Do not identify "
        "unknown people by real-world identity—describe visible roles or clothing. "
        "For WHY questions, clearly distinguish visual evidence from plausible inference. "
        "Choose one short, concrete, visible noun phrase for grounding_concept so a "
        "segmentation model can verify it (examples: person, car, dog, cup). Choose the "
        "single frame that best supports the answer. caveat must contain only a limitation "
        "or uncertainty; use an empty string when none is needed. Be direct and useful."
    )
    content = (
        f"Sample timeline: {timeline}\n"
        f"Question: {question}\n"
        "Answer from visual evidence across these frames. Mention uncertainty when the "
        "sampled frames cannot establish the answer."
    )
    response = httpx.post(
        f"{ollama_url.rstrip('/')}/api/chat",
        json={
            "model": ollama_model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0.2},
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": content,
                    "images": [_vision_jpeg(frame) for frame in frames],
                },
            ],
        },
        timeout=300.0,
    )
    response.raise_for_status()
    raw = response.json()["message"]["content"]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid structured output: {raw[:300]}") from exc
    index = int(parsed["evidence_frame_index"])
    if not 0 <= index < len(frames):
        raise RuntimeError(f"Ollama selected invalid evidence frame {index}")
    return parsed


def _draw_grounding(
    frame: np.ndarray,
    instances: list[Any],
    concept: str,
    timestamp_s: float,
    output_path: Path,
) -> None:
    overlay = frame.copy()
    for idx, instance in enumerate(instances):
        color = _PALETTE[idx % len(_PALETTE)]
        points = np.array(instance.polygon, dtype=np.int32)
        if len(points) >= 3:
            cv2.fillPoly(overlay, [points], color)
            cv2.polylines(frame, [points], True, color, 3, cv2.LINE_AA)
        x, y, w, h = (round(v) for v in instance.bbox)
        cv2.rectangle(frame, (x, y), (x + w, y + h), color, 3)
        cv2.putText(
            frame,
            f"{concept} {instance.score:.2f}",
            (x + 4, max(26, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
            cv2.LINE_AA,
        )
    frame = cv2.addWeighted(overlay, 0.28, frame, 0.72, 0)
    cv2.putText(
        frame,
        f"SAM3 evidence @ {timestamp_s:.1f}s",
        (22, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 90]):
        raise RuntimeError("failed to write SAM3 evidence image")


def visual_chat(
    video_id: str,
    question: str,
    current_time_s: float | None,
    evidence_id: str,
) -> dict[str, Any]:
    """Answer one visual question, then ground the answer with SAM3."""
    video_dir = _video_dir(video_id)
    video = video_dir / "playback.mp4"
    evidence = video_dir / "evidence" / f"{UUID(evidence_id)}.jpg"
    ollama_url, _ = resolve_ollama_url()
    ollama_model = os.getenv("OLLAMA_VISION_MODEL", "gemma3:4b")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video.name}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or frame_count <= 0:
        cap.release()
        raise RuntimeError("video has invalid frame metadata")
    duration = frame_count / fps
    timestamps = _sample_timestamps(duration, current_time_s)
    frames = [_reasoning_frame(_read_frame(cap, t)) for t in timestamps]
    cap.release()

    started = time.monotonic()
    reasoning = _ask_ollama(question, frames, timestamps, ollama_url, ollama_model)
    evidence_index = int(reasoning["evidence_frame_index"])
    evidence_time = timestamps[evidence_index]
    concept = str(reasoning.get("grounding_concept") or "").strip()

    # Re-read only the selected evidence frame at full resolution. Keeping five
    # 4K frames alive during Ollama + SAM3 can consume hundreds of MB per call.
    cap = cv2.VideoCapture(str(video))
    evidence_frame = _read_frame(cap, evidence_time)
    cap.release()

    instances: list[Any] = []
    if concept:
        working = evidence.with_suffix(".working.jpg")
        working.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(working), evidence_frame)
        try:
            instances = get_provider("sam3").predict_concept(
                str(working), concept, threshold=0.3, max_instances=30
            )
        finally:
            working.unlink(missing_ok=True)
    _draw_grounding(evidence_frame, instances, concept, evidence_time, evidence)

    log.info(
        "video_visual_chat",
        extra={
            "question": question,
            "grounding_concept": concept,
            "detections": len(instances),
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
            "ollama_model": ollama_model,
        },
    )
    return {
        "answer": str(reasoning["answer"]),
        "confidence": str(reasoning["confidence"]),
        "caveat": str(reasoning.get("caveat") or "") or None,
        "grounding_concept": concept or None,
        "evidence_timestamp_s": evidence_time,
        "detection_count": len(instances),
        "detection_scores": [round(float(item.score), 4) for item in instances],
        "sampled_timestamps_s": timestamps,
        "ollama_model": ollama_model,
        "segmentation_model": "sam3",
    }
