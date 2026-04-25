"""Image routes: upload, list, get, embed (background), predict (auto-annotate), serve files."""
from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from PIL import Image as PILImage
from sqlmodel import Session, func, select

from anvil_api.auth import CurrentUser, SessionDep
from anvil_api.db import engine
from anvil_api.ml_client import ml_client
from anvil_api.models import Annotation, Dataset, Image, ImageStatus, ModelVersion, ModelVersionStatus, Project
from anvil_api.schemas import ImageOut, PredictIn, PredictOut
from anvil_api.storage import storage

router = APIRouter(tags=["images"])


def _check_dataset(session: Session, project_id: UUID, dataset_id: UUID, user) -> Dataset:
    p = session.get(Project, project_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    return d


def _to_out(session: Session, img: Image) -> ImageOut:
    ann_count = session.exec(
        select(func.count()).select_from(Annotation).where(Annotation.image_id == img.id)
    ).one()
    return ImageOut(
        id=img.id,
        dataset_id=img.dataset_id,
        filename=img.filename,
        width=img.width,
        height=img.height,
        bytes=img.bytes,
        status=img.status,
        error=img.error,
        thumbnail_url=storage.signed_url(img.thumbnail_key) if img.thumbnail_key else None,
        image_url=storage.signed_url(img.storage_key),
        uncertainty=img.uncertainty,
        annotation_count=ann_count,
    )


# --------- Upload ---------
@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/images",
    response_model=list[ImageOut],
    status_code=status.HTTP_201_CREATED,
)
async def upload_images(
    project_id: UUID,
    dataset_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> list[ImageOut]:
    d = _check_dataset(session, project_id, dataset_id, user)

    out: list[Image] = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower() or ".jpg"
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            raise HTTPException(415, f"unsupported image type: {ext}")

        data = await f.read()
        img_id = uuid4()
        key = f"datasets/{d.id}/images/{img_id}{ext}"
        storage.put(key, data)

        # Probe dimensions + write thumbnail (256px max side)
        try:
            with PILImage.open(io.BytesIO(data)) as im:
                w, h = im.size
                im.thumbnail((256, 256))
                tbuf = io.BytesIO()
                im.convert("RGB").save(tbuf, format="JPEG", quality=80)
                tkey = f"datasets/{d.id}/thumbs/{img_id}.jpg"
                storage.put(tkey, tbuf.getvalue())
        except Exception as e:
            raise HTTPException(400, f"invalid image: {e}") from e

        rec = Image(
            id=img_id,
            dataset_id=d.id,
            filename=f.filename,
            storage_key=key,
            thumbnail_key=tkey,
            width=w,
            height=h,
            bytes=len(data),
            status=ImageStatus.UPLOADED,
        )
        session.add(rec)
        out.append(rec)

    session.commit()
    for rec in out:
        session.refresh(rec)
        background.add_task(_run_embed, rec.id)

    return [_to_out(session, r) for r in out]


def _run_embed(image_id: UUID) -> None:
    """Background: call ML /embed and persist embedding key + status."""
    import asyncio
    asyncio.run(_embed_async(image_id))


async def _embed_async(image_id: UUID) -> None:
    with Session(engine) as session:
        img = session.get(Image, image_id)
        if not img:
            return
        img.status = ImageStatus.EMBEDDING
        session.add(img)
        session.commit()

        try:
            embedding_key = f"datasets/{img.dataset_id}/embeddings/{img.id}.npz"
            await ml_client.embed(
                image_path=storage.absolute_path(img.storage_key),
                embedding_path=storage.absolute_path(embedding_key),
            )
            img.embedding_key = embedding_key
            img.status = ImageStatus.READY
            img.error = None
        except Exception as e:  # pragma: no cover
            img.status = ImageStatus.FAILED
            img.error = str(e)[:500]
        session.add(img)
        session.commit()


# --------- List / Get ---------
@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/images",
    response_model=list[ImageOut],
)
def list_images(
    project_id: UUID, dataset_id: UUID, session: SessionDep, user: CurrentUser,
    status_filter: ImageStatus | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[ImageOut]:
    _check_dataset(session, project_id, dataset_id, user)
    q = select(Image).where(Image.dataset_id == dataset_id)
    if status_filter:
        q = q.where(Image.status == status_filter)
    q = q.order_by(Image.uploaded_at).offset(offset).limit(limit)
    return [_to_out(session, i) for i in session.exec(q).all()]


@router.get("/images/{image_id}", response_model=ImageOut)
def get_image(image_id: UUID, session: SessionDep, user: CurrentUser) -> ImageOut:
    img = session.get(Image, image_id)
    if not img:
        raise HTTPException(404, "image not found")
    # ownership check
    d = session.get(Dataset, img.dataset_id)
    p = session.get(Project, d.project_id) if d else None
    if not p or p.owner_id != user.id:
        raise HTTPException(404, "image not found")
    return _to_out(session, img)


# --------- File serving (local provider only) ---------
@router.get("/files/{key:path}")
def serve_file(key: str) -> Response:
    if not storage.exists(key):
        raise HTTPException(404, "not found")
    data = storage.get(key)
    # naive content-type
    ext = Path(key).suffix.lower()
    ctype = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".bmp": "image/bmp",
        ".npz": "application/octet-stream",
    }.get(ext, "application/octet-stream")
    return Response(content=data, media_type=ctype)


# --------- Predict (auto-annotate) ---------
@router.post("/images/{image_id}/predict", response_model=PredictOut)
async def predict(
    image_id: UUID, body: PredictIn, session: SessionDep, user: CurrentUser
) -> PredictOut:
    img = session.get(Image, image_id)
    if not img:
        raise HTTPException(404, "image not found")
    d = session.get(Dataset, img.dataset_id)
    p = session.get(Project, d.project_id) if d else None
    if not p or p.owner_id != user.id:
        raise HTTPException(404, "image not found")
    if img.status != ImageStatus.READY:
        raise HTTPException(409, f"image not ready (status={img.status})")

    # Resolve adapter: explicit model_version_id wins, else project's active
    adapter_path: str | None = None
    mv_id = body.model_version_id or p.active_model_version_id
    if mv_id:
        mv = session.get(ModelVersion, mv_id)
        if mv and mv.project_id == p.id and mv.status == ModelVersionStatus.READY and mv.adapter_key:
            adapter_path = storage.absolute_path(mv.adapter_key)

    res = await ml_client.predict(
        image_path=storage.absolute_path(img.storage_key),
        embedding_path=storage.absolute_path(img.embedding_key) if img.embedding_key else None,
        points=body.points,
        point_labels=body.point_labels,
        box=body.box,
        adapter_path=adapter_path,
    )
    return PredictOut(**res)
