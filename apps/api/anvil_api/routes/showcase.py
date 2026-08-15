"""SAM3 video showcase: local playback plus visual question answering.

This intentionally creates no annotations or training data. It is a focused
inference experience that combines a local Ollama vision model for language
reasoning with SAM3 for mask-level visual grounding.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from urllib.parse import unquote
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlmodel import Session, select

from anvil_api.auth import RequireAnyAuthed, RequireLabeler, SessionDep
from anvil_api.db import engine
from anvil_api.ml_client import ml_client
from anvil_api.models import ShowcaseVideo
from anvil_api.schemas import ShowcaseChatIn, ShowcaseChatOut, ShowcaseVideoOut
from anvil_api.storage import storage

router = APIRouter(prefix="/showcase", tags=["showcase"])

_ALLOWED_VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".webm"}
_MAX_VIDEO_BYTES = 1024 * 1024 * 1024


def _video_out(video: ShowcaseVideo) -> ShowcaseVideoOut:
    playback_url = (
        storage.signed_url(video.playback_key, expires_seconds=8 * 3600)
        if video.playback_key and storage.exists(video.playback_key)
        else None
    )
    poster_url = (
        storage.signed_url(video.poster_key, expires_seconds=8 * 3600)
        if video.poster_key and storage.exists(video.poster_key)
        else None
    )
    return ShowcaseVideoOut(
        id=video.id,
        filename=video.filename,
        bytes=video.bytes,
        width=video.width,
        height=video.height,
        fps=video.fps,
        duration_s=video.duration_s,
        frame_count=video.frame_count,
        codec=video.codec,
        status=video.status,
        error=video.error,
        playback_url=playback_url,
        poster_url=poster_url,
        created_at=video.created_at,
    )


def _prepare_video(video_id: UUID) -> None:
    """Background preparation: probe + browser-compatible H.264 transcode."""
    async def run() -> None:
        with Session(engine) as session:
            video = session.get(ShowcaseVideo, video_id)
            if not video:
                return
            try:
                metadata = await ml_client.prepare_video(
                    video_id=str(video.id),
                )
                video.width = metadata["width"]
                video.height = metadata["height"]
                video.fps = metadata["fps"]
                video.duration_s = metadata["duration_s"]
                video.frame_count = metadata["frame_count"]
                video.codec = metadata["codec"]
                video.status = "ready"
                video.error = None
            except Exception as exc:
                video.status = "failed"
                video.error = f"{type(exc).__name__}: {exc}"[:2000]
            session.add(video)
            session.commit()

    asyncio.run(run())


@router.get("/runtime")
async def showcase_runtime(_user: RequireAnyAuthed) -> dict[str, object]:
    """Report whether the two local inference engines are available."""
    ml_ok = False
    ollama_ok = False
    ollama_models: list[str] = []
    try:
        readiness = await ml_client.showcase_ready()
        ml_ok = bool(readiness.get("sam3") and readiness.get("ffmpeg"))
        ollama_ok = bool(readiness.get("ollama"))
        ollama_models = list(readiness.get("ollama_models") or [])
        ollama_model = str(readiness.get("ollama_model") or "gemma3:4b")
    except Exception:
        ollama_model = "gemma3:4b"
    return {
        "sam3": ml_ok,
        "ollama": ollama_ok,
        "ollama_model": ollama_model,
        "ollama_models": ollama_models,
    }


@router.get("/videos", response_model=list[ShowcaseVideoOut])
def list_showcase_videos(
    session: SessionDep, _user: RequireAnyAuthed,
) -> list[ShowcaseVideoOut]:
    rows = session.exec(
        select(ShowcaseVideo).order_by(ShowcaseVideo.created_at.desc())  # type: ignore[union-attr]
    ).all()
    return [_video_out(row) for row in rows]


@router.get("/videos/{video_id}", response_model=ShowcaseVideoOut)
def get_showcase_video(
    video_id: UUID, session: SessionDep, _user: RequireAnyAuthed,
) -> ShowcaseVideoOut:
    video = session.get(ShowcaseVideo, video_id)
    if not video:
        raise HTTPException(404, "showcase video not found")
    return _video_out(video)


@router.post(
    "/videos",
    response_model=ShowcaseVideoOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_showcase_video(
    session: SessionDep,
    user: RequireLabeler,
    background: BackgroundTasks,
    request: Request,
) -> ShowcaseVideoOut:
    raw_name = request.headers.get("X-Filename")
    if not raw_name:
        raise HTTPException(400, "X-Filename header is required")
    filename = Path(unquote(raw_name)).name
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_VIDEO_EXTS:
        raise HTTPException(415, f"unsupported video type: {ext or '(none)'}")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_VIDEO_BYTES:
                raise HTTPException(413, "video exceeds the 1GB local showcase limit")
        except ValueError as exc:
            raise HTTPException(400, "invalid Content-Length header") from exc

    video_id = uuid4()
    prefix = f"showcase/videos/{video_id}"
    source_key = f"{prefix}/source{ext}"
    playback_key = f"{prefix}/playback.mp4"
    poster_key = f"{prefix}/poster.jpg"
    source_path = Path(storage.absolute_path(source_key))
    size = 0
    try:
        with source_path.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > _MAX_VIDEO_BYTES:
                    raise HTTPException(413, "video exceeds the 1GB local showcase limit")
                output.write(chunk)
    except Exception:
        source_path.unlink(missing_ok=True)
        raise
    if size <= 0:
        source_path.unlink(missing_ok=True)
        raise HTTPException(400, "video is empty")

    video = ShowcaseVideo(
        id=video_id,
        filename=filename,
        source_key=source_key,
        playback_key=playback_key,
        poster_key=poster_key,
        bytes=size,
        status="preparing",
        uploaded_by=user.id,
    )
    session.add(video)
    session.commit()
    session.refresh(video)
    background.add_task(_prepare_video, video.id)
    return _video_out(video)


@router.delete("/videos/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_showcase_video(
    video_id: UUID, session: SessionDep, _user: RequireLabeler,
) -> None:
    video = session.get(ShowcaseVideo, video_id)
    if not video:
        raise HTTPException(404, "showcase video not found")
    root = Path(storage.absolute_path(f"showcase/videos/{video.id}/marker")).parent
    video.status = "deleting"
    session.add(video)
    session.commit()
    try:
        shutil.rmtree(root)
    except FileNotFoundError:
        pass
    except OSError as exc:
        video.status = "failed"
        video.error = f"storage deletion failed: {exc}"[:2000]
        session.add(video)
        session.commit()
        raise HTTPException(500, video.error) from exc
    session.delete(video)
    session.commit()


@router.post("/videos/{video_id}/chat", response_model=ShowcaseChatOut)
async def chat_about_video(
    video_id: UUID,
    body: ShowcaseChatIn,
    session: SessionDep,
    _user: RequireAnyAuthed,
) -> ShowcaseChatOut:
    video = session.get(ShowcaseVideo, video_id)
    if not video:
        raise HTTPException(404, "showcase video not found")
    if video.status != "ready" or not video.playback_key:
        raise HTTPException(409, f"video is not ready (status={video.status})")
    # Do not hold a database connection while this request waits behind the ML
    # semaphore and performs ~1 minute of local vision inference.
    session.close()

    evidence_id = uuid4()
    evidence_key = f"showcase/videos/{video.id}/evidence/{evidence_id}.jpg"
    try:
        result = await ml_client.video_chat(
            video_id=str(video.id),
            question=body.question.strip(),
            current_time_s=body.current_time_s,
            evidence_id=str(evidence_id),
        )
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:1000]
        raise HTTPException(502, f"local visual inference failed: {detail}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"local visual inference unavailable: {exc}") from exc

    result["evidence_url"] = (
        storage.signed_url(evidence_key, expires_seconds=8 * 3600)
        if storage.exists(evidence_key)
        else None
    )
    return ShowcaseChatOut(**result)
