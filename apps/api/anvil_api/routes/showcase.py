"""SAM3 object discovery and personal-use video removal."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from pathlib import Path
from urllib.parse import unquote
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from anvil_api.auth import RequireAnyAuthed, RequireLabeler, SessionDep
from anvil_api.config import settings
from anvil_api.db import engine
from anvil_api.ml_client import ml_client
from anvil_api.models import ShowcaseRemovalJob, ShowcaseVideo, utcnow
from anvil_api.schemas import (
    ShowcaseDiscoverIn,
    ShowcaseDiscoverOut,
    ShowcaseRemovalIn,
    ShowcaseRemovalOut,
    ShowcaseVideoOut,
)
from anvil_api.storage import storage

router = APIRouter(prefix="/showcase", tags=["showcase"])

_ALLOWED_VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".webm"}
_MAX_VIDEO_BYTES = 1024 * 1024 * 1024
_PERSONAL_WORKER_HEADERS = {
    "X-Anvil-Personal": settings.personal_video_worker_token
}
_import_locks: dict[UUID, asyncio.Lock] = {}


async def _personal_worker() -> tuple[str, dict[str, object]]:
    configured = settings.personal_video_worker_url.rstrip("/")
    candidates = (
        [configured]
        if configured and configured != "auto"
        else [
            "http://host.docker.internal:8002",
            "http://host.rancher-desktop.internal:8002",
        ]
    )
    async with httpx.AsyncClient(timeout=3.0) as client:
        for base in candidates:
            try:
                response = await client.get(f"{base}/health")
                response.raise_for_status()
                health = response.json()
                if health.get("propainter"):
                    return base, health
            except (httpx.HTTPError, ValueError):
                continue
    raise HTTPException(
        503,
        "Personal ProPainter worker is not running. Start: "
        "python3 scripts/personal_video_worker.py",
    )


async def _dispatch_removal(
    client: httpx.AsyncClient,
    worker_base: str,
    job: ShowcaseRemovalJob,
    video: ShowcaseVideo,
) -> None:
    objects = job.mask_rle.get("objects")
    if not isinstance(objects, list) or not objects:
        raise HTTPException(409, "removal job has no stored object masks")
    source_url = (
        "http://127.0.0.1:8000"
        + storage.signed_url(video.source_key, expires_seconds=30 * 24 * 3600)
    )
    response = await client.post(
        f"{worker_base}/jobs",
        headers=_PERSONAL_WORKER_HEADERS,
        json={
            "job_id": str(job.id),
            "source_url": source_url,
            "start_s": job.start_s,
            "end_s": job.end_s,
            "seed_s": job.seed_s,
            "mask_rles": objects,
            "manual_mask_png": job.mask_rle.get("manual_mask_png"),
            "source_width": video.width,
            "source_height": video.height,
            "fps": 30,
            "width": 432,
            "height": 240,
        },
    )
    # 409 means a previous ambiguous request was accepted; dispatch is
    # intentionally idempotent by job UUID.
    if response.status_code != 409:
        response.raise_for_status()


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


def _removal_out(job: ShowcaseRemovalJob) -> ShowcaseRemovalOut:
    output_url = (
        storage.signed_url(job.output_key, expires_seconds=8 * 3600)
        if job.output_key and storage.exists(job.output_key)
        else None
    )
    return ShowcaseRemovalOut(
        id=job.id,
        video_id=job.video_id,
        concept=job.concept,
        start_s=job.start_s,
        end_s=job.end_s,
        seed_s=job.seed_s,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        detail=job.detail,
        elapsed_seconds=max(0.0, job.active_seconds),
        output_url=output_url,
        error=job.error,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


async def _apply_remote_job(
    session: Session,
    job: ShowcaseRemovalJob,
    remote: dict[str, object],
    client: httpx.AsyncClient,
    worker_base: str,
) -> None:
    lock = _import_locks.setdefault(job.id, asyncio.Lock())
    async with lock:
        session.refresh(job)
        remote_status = str(remote["status"])
        # Terminal database state is monotonic. A stale in-flight GET must not
        # resurrect work after Cancel or overwrite an already imported output.
        if job.status in {"cancelled", "completed"} and remote_status != job.status:
            return
        job.status = remote_status
        job.stage = str(remote["stage"])
        job.progress = float(remote["progress"])
        job.active_seconds = float(remote.get("active_seconds") or job.active_seconds)
        job.detail = remote.get("detail")  # type: ignore[assignment]
        job.error = remote.get("error")  # type: ignore[assignment]
        if job.status == "completed" and not job.output_key:
            output_key = f"showcase/videos/{job.video_id}/edits/{job.id}.mp4"
            output_path = Path(storage.absolute_path(output_key))
            temp_path = output_path.with_suffix(f".{uuid4()}.tmp")
            try:
                async with client.stream(
                    "GET",
                    f"{worker_base}/jobs/{job.id}/output",
                    headers=_PERSONAL_WORKER_HEADERS,
                ) as download:
                    download.raise_for_status()
                    with temp_path.open("wb") as target:
                        async for chunk in download.aiter_bytes():
                            target.write(chunk)
                os.replace(temp_path, output_path)
            finally:
                temp_path.unlink(missing_ok=True)
            job.output_key = output_key
            job.completed_at = utcnow()
            # Persist the durable API-owned output before deleting the worker
            # copy. A crash after this commit leaves harmless duplicate data;
            # the reverse order could lose the only completed overnight result.
            session.add(job)
            session.commit()
            with contextlib.suppress(httpx.HTTPError):
                await client.delete(
                    f"{worker_base}/jobs/{job.id}",
                    headers=_PERSONAL_WORKER_HEADERS,
                )
        session.add(job)
        session.commit()
        session.refresh(job)


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
    """Report whether SAM3 and the personal removal backend are available."""
    ml_ok = False
    try:
        readiness = await ml_client.showcase_ready()
        ml_ok = bool(readiness.get("sam3") and readiness.get("ffmpeg"))
    except Exception:
        pass
    personal_worker: dict[str, object] | None = None
    with contextlib.suppress(HTTPException):
        _base, personal_worker = await _personal_worker()
    return {
        "sam3": ml_ok,
        "personal_worker": personal_worker,
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
    jobs = session.exec(
        select(ShowcaseRemovalJob).where(ShowcaseRemovalJob.video_id == video.id)
    ).all()
    with contextlib.suppress(HTTPException, httpx.HTTPError):
        worker_base, _health = asyncio.run(_personal_worker())
        with httpx.Client(timeout=5.0) as client:
            for job in jobs:
                with contextlib.suppress(httpx.HTTPError):
                    client.delete(
                        f"{worker_base}/jobs/{job.id}",
                        headers=_PERSONAL_WORKER_HEADERS,
                    )
    for job in jobs:
        session.delete(job)
    session.delete(video)
    session.commit()


@router.post(
    "/videos/{video_id}/discover", response_model=ShowcaseDiscoverOut
)
async def discover_video_objects(
    video_id: UUID,
    body: ShowcaseDiscoverIn,
    session: SessionDep,
    _user: RequireAnyAuthed,
) -> ShowcaseDiscoverOut:
    video = session.get(ShowcaseVideo, video_id)
    if not video:
        raise HTTPException(404, "showcase video not found")
    if video.status != "ready":
        raise HTTPException(409, f"video is not ready (status={video.status})")
    if video.duration_s is not None and body.timestamp_s > video.duration_s:
        raise HTTPException(400, "timestamp is beyond the video duration")
    session.close()
    try:
        result = await ml_client.discover_video_frame(
            str(video_id), body.timestamp_s, body.concept, body.threshold
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, exc.response.text[:1000]) from exc
    return ShowcaseDiscoverOut(**result)


@router.post(
    "/videos/{video_id}/removals",
    response_model=ShowcaseRemovalOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_removal(
    video_id: UUID,
    body: ShowcaseRemovalIn,
    session: SessionDep,
    user: RequireLabeler,
) -> ShowcaseRemovalOut:
    video = session.get(ShowcaseVideo, video_id)
    if not video:
        raise HTTPException(404, "showcase video not found")
    if video.status != "ready" or not video.playback_key:
        raise HTTPException(409, f"video is not ready (status={video.status})")
    if body.end_s <= body.start_s:
        raise HTTPException(400, "end_s must be after start_s")
    if body.end_s - body.start_s > 600.01:
        raise HTTPException(400, "local removal is limited to 10-minute videos")
    if not body.start_s <= body.seed_s <= body.end_s:
        raise HTTPException(400, "selected object frame must be inside clip range")
    if video.duration_s is not None and body.end_s > video.duration_s + 0.01:
        raise HTTPException(400, "clip end is beyond the video duration")
    if not video.width or not video.height:
        raise HTTPException(409, "video dimensions are unavailable")
    existing = session.get(ShowcaseRemovalJob, body.request_id)
    if existing:
        if existing.video_id != video.id:
            raise HTTPException(409, "request_id already belongs to another video")
        return _removal_out(existing)

    worker_base, _health = await _personal_worker()
    job = ShowcaseRemovalJob(
        id=body.request_id,
        video_id=video.id,
        concept=body.concept.strip(),
        start_s=body.start_s,
        end_s=body.end_s,
        seed_s=body.seed_s,
        selected_score=max(body.selected_scores) if body.selected_scores else None,
        mask_rle={
            "objects": body.mask_rles,
            "manual_mask_png": body.manual_mask_png,
        },
        created_by=user.id,
    )
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.get(ShowcaseRemovalJob, body.request_id)
        if existing:
            return _removal_out(existing)
        raise
    session.refresh(job)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await _dispatch_removal(client, worker_base, job, video)
    except httpx.HTTPError as exc:
        # Keep queued: GET reconciliation can safely retry this idempotent
        # dispatch after an API restart or ambiguous network failure.
        job.detail = f"waiting to dispatch to personal worker: {exc}"[:1000]
        session.add(job)
        session.commit()
    return _removal_out(job)


@router.get("/removals/{job_id}", response_model=ShowcaseRemovalOut)
async def get_removal(
    job_id: UUID, session: SessionDep, _user: RequireAnyAuthed
) -> ShowcaseRemovalOut:
    job = session.get(ShowcaseRemovalJob, job_id)
    if not job:
        raise HTTPException(404, "removal job not found")
    if job.status in {"completed", "failed", "cancelled"}:
        return _removal_out(job)
    try:
        worker_base, _health = await _personal_worker()
    except HTTPException:
        if job.status in {"queued", "running", "pausing"}:
            job.status = "interrupted"
            job.stage = "interrupted"
            job.detail = (
                "Personal worker is offline. Start it, then choose Resume job."
            )
            session.add(job)
            session.commit()
            session.refresh(job)
        return _removal_out(job)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{worker_base}/jobs/{job.id}", headers=_PERSONAL_WORKER_HEADERS
            )
            if response.status_code == 404:
                if job.status == "queued":
                    video = session.get(ShowcaseVideo, job.video_id)
                    if not video:
                        raise HTTPException(404, "source video no longer exists")
                    await _dispatch_removal(client, worker_base, job, video)
                    response = await client.get(
                        f"{worker_base}/jobs/{job.id}",
                        headers=_PERSONAL_WORKER_HEADERS,
                    )
                else:
                    job.status = "interrupted"
                    job.stage = "interrupted"
                    job.detail = "Worker has no active state. Choose Resume job."
                    session.add(job)
                    session.commit()
                    session.refresh(job)
                    return _removal_out(job)
            response.raise_for_status()
            remote = response.json()
            await _apply_remote_job(session, job, remote, client, worker_base)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"personal worker unavailable: {exc}") from exc
    return _removal_out(job)


@router.post("/removals/{job_id}/pause", response_model=ShowcaseRemovalOut)
async def pause_removal(
    job_id: UUID, session: SessionDep, _user: RequireLabeler
) -> ShowcaseRemovalOut:
    job = session.get(ShowcaseRemovalJob, job_id)
    if not job:
        raise HTTPException(404, "removal job not found")
    if job.status not in {"queued", "running", "pausing"}:
        return _removal_out(job)
    worker_base, _health = await _personal_worker()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{worker_base}/jobs/{job.id}/pause",
                headers=_PERSONAL_WORKER_HEADERS,
            )
            response.raise_for_status()
            remote = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not pause personal worker: {exc}") from exc
    async with httpx.AsyncClient(timeout=30.0) as client:
        await _apply_remote_job(session, job, remote, client, worker_base)
    return _removal_out(job)


@router.post("/removals/{job_id}/resume", response_model=ShowcaseRemovalOut)
async def resume_removal(
    job_id: UUID, session: SessionDep, _user: RequireLabeler
) -> ShowcaseRemovalOut:
    job = session.get(ShowcaseRemovalJob, job_id)
    if not job:
        raise HTTPException(404, "removal job not found")
    if job.status in {"completed", "cancelled"}:
        return _removal_out(job)
    video = session.get(ShowcaseVideo, job.video_id)
    if not video:
        raise HTTPException(404, "source video no longer exists")
    worker_base, _health = await _personal_worker()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{worker_base}/jobs/{job.id}/resume",
                headers=_PERSONAL_WORKER_HEADERS,
            )
            if response.status_code == 404:
                await _dispatch_removal(client, worker_base, job, video)
                response = await client.get(
                    f"{worker_base}/jobs/{job.id}",
                    headers=_PERSONAL_WORKER_HEADERS,
                )
            response.raise_for_status()
            remote = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not resume personal worker: {exc}") from exc
    async with httpx.AsyncClient(timeout=30.0) as client:
        await _apply_remote_job(session, job, remote, client, worker_base)
    return _removal_out(job)


@router.delete(
    "/removals/{job_id}",
    response_model=ShowcaseRemovalOut,
)
async def cancel_removal(
    job_id: UUID, session: SessionDep, _user: RequireLabeler
) -> ShowcaseRemovalOut:
    job = session.get(ShowcaseRemovalJob, job_id)
    if not job:
        raise HTTPException(404, "removal job not found")
    lock = _import_locks.setdefault(job.id, asyncio.Lock())
    async with lock:
        session.refresh(job)
        if job.status in {"queued", "running", "pausing", "interrupted"}:
            with contextlib.suppress(HTTPException, httpx.HTTPError):
                worker_base, _health = await _personal_worker()
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.delete(
                        f"{worker_base}/jobs/{job.id}",
                        headers=_PERSONAL_WORKER_HEADERS,
                    )
            job.status = "cancelled"
            job.stage = "cancelled"
            job.detail = "Cancelled by user"
            session.add(job)
            session.commit()
            session.refresh(job)
    return _removal_out(job)


@router.get(
    "/videos/{video_id}/removals", response_model=list[ShowcaseRemovalOut]
)
async def list_removals(
    video_id: UUID, session: SessionDep, _user: RequireAnyAuthed
) -> list[ShowcaseRemovalOut]:
    if not session.get(ShowcaseVideo, video_id):
        raise HTTPException(404, "showcase video not found")
    rows = session.exec(
        select(ShowcaseRemovalJob)
        .where(ShowcaseRemovalJob.video_id == video_id)
        .order_by(ShowcaseRemovalJob.created_at.desc())  # type: ignore[union-attr]
    ).all()
    return [_removal_out(row) for row in rows]
