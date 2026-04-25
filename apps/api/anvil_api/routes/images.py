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
from anvil_api.models import (
    Annotation,
    Comment,
    Dataset,
    Image,
    ImageStatus,
    ModelVersion,
    ModelVersionStatus,
    Project,
    ReviewStatus,
)
from anvil_api.schemas import ImageOut, PredictIn, PredictOut, ReviewActionIn
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
    cmt_total = session.exec(
        select(func.count()).select_from(Comment).where(Comment.image_id == img.id)
    ).one()
    cmt_open = session.exec(
        select(func.count()).select_from(Comment)
        .where(Comment.image_id == img.id, Comment.resolved == False)  # noqa: E712
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
        review_status=img.review_status,
        submitted_at=img.submitted_at,
        reviewed_at=img.reviewed_at,
        review_reason=img.review_reason,
        comment_count=cmt_total,
        open_comment_count=cmt_open,
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
    review_status: ReviewStatus | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[ImageOut]:
    _check_dataset(session, project_id, dataset_id, user)
    q = select(Image).where(Image.dataset_id == dataset_id)
    if status_filter:
        q = q.where(Image.status == status_filter)
    if review_status:
        q = q.where(Image.review_status == review_status)
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


# --------- Review flow (submit / approve / reject / reopen) ---------
def _check_image_owner(session: Session, image_id: UUID, user) -> Image:
    img = session.get(Image, image_id)
    if not img:
        raise HTTPException(404, "image not found")
    d = session.get(Dataset, img.dataset_id)
    p = session.get(Project, d.project_id) if d else None
    if not p or p.owner_id != user.id:
        raise HTTPException(404, "image not found")
    return img


@router.patch("/images/{image_id}/review", response_model=ImageOut)
def review_action(
    image_id: UUID, body: ReviewActionIn, session: SessionDep, user: CurrentUser,
) -> ImageOut:
    """Drive the review state machine.

    Valid transitions:
      submit:  new | in_progress | rejected → submitted
      approve: submitted                    → approved
      reject:  submitted                    → rejected (reason required, attached as comment)
      reopen:  approved | rejected          → in_progress
    """
    img = _check_image_owner(session, image_id, user)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    action = (body.action or "").strip().lower()

    if action == "submit":
        if img.review_status not in (ReviewStatus.NEW, ReviewStatus.IN_PROGRESS, ReviewStatus.REJECTED):
            raise HTTPException(409, f"cannot submit from {img.review_status}")
        # Require at least one annotation before submitting
        n_ann = session.exec(
            select(func.count()).select_from(Annotation).where(Annotation.image_id == img.id)
        ).one()
        if n_ann == 0:
            raise HTTPException(400, "annotate at least one object before submitting for review")
        img.review_status = ReviewStatus.SUBMITTED
        img.submitted_at = now
        img.submitted_by = user.id
    elif action == "approve":
        if img.review_status != ReviewStatus.SUBMITTED:
            raise HTTPException(409, f"can only approve a submitted image (currently {img.review_status})")
        img.review_status = ReviewStatus.APPROVED
        img.reviewed_at = now
        img.reviewed_by = user.id
        img.review_reason = None
    elif action == "reject":
        if img.review_status != ReviewStatus.SUBMITTED:
            raise HTTPException(409, f"can only reject a submitted image (currently {img.review_status})")
        if not body.reason or not body.reason.strip():
            raise HTTPException(400, "reject requires a reason")
        img.review_status = ReviewStatus.REJECTED
        img.reviewed_at = now
        img.reviewed_by = user.id
        img.review_reason = body.reason.strip()
        # Attach the reason as a general comment for visibility
        session.add(Comment(
            image_id=img.id, author_id=user.id,
            body=f"[Reject] {body.reason.strip()}",
            x=None, y=None, resolved=False,
        ))
    elif action == "reopen":
        if img.review_status not in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
            raise HTTPException(409, f"cannot reopen from {img.review_status}")
        img.review_status = ReviewStatus.IN_PROGRESS
        img.reviewed_at = None
        img.reviewed_by = None
        img.review_reason = None
    else:
        raise HTTPException(400, f"unknown action: {action!r}")
    session.add(img)
    session.commit()
    session.refresh(img)
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


# --------- Active learning ---------

@router.get("/projects/{project_id}/datasets/{dataset_id}/next-uncertain")
async def next_uncertain(
    project_id: UUID, dataset_id: UUID, session: SessionDep, user: CurrentUser,
    batch_size: int = 10,
):
    """Return the un-annotated image the active model is least sure about.

    Lazily scores up to `batch_size` un-scored images per call, then returns the
    overall highest-uncertainty candidate in the dataset. Idempotent + cheap on
    repeat calls.
    """
    _check_dataset(session, project_id, dataset_id, user)
    p = session.get(Project, project_id)
    assert p is not None

    annotated_q = select(Annotation.image_id).join(Image).where(Image.dataset_id == dataset_id)

    # 1) score up to batch_size unscored images first
    unscored_q = (
        select(Image)
        .where(
            Image.dataset_id == dataset_id,
            Image.status == ImageStatus.READY,
            Image.uncertainty.is_(None),  # type: ignore[union-attr]
            Image.id.not_in(annotated_q),  # type: ignore[union-attr]
        )
        .limit(batch_size)
    )
    unscored = list(session.exec(unscored_q).all())

    adapter_path = None
    if p.active_model_version_id:
        mv = session.get(ModelVersion, p.active_model_version_id)
        if mv and mv.status == ModelVersionStatus.READY and mv.adapter_key:
            adapter_path = storage.absolute_path(mv.adapter_key)

    scored = 0
    for img in unscored:
        if not img.embedding_key:
            continue
        try:
            r = await ml_client.score(
                image_path=storage.absolute_path(img.storage_key),
                embedding_path=storage.absolute_path(img.embedding_key),
                adapter_path=adapter_path,
            )
            img.uncertainty = float(r["uncertainty"])
            session.add(img)
            scored += 1
        except Exception as e:
            print(f"[anvil-api] uncertainty score failed for {img.id}: {e}")
    if scored:
        session.commit()

    # 2) pick the most uncertain across all (already-scored) un-annotated images
    best_q = (
        select(Image)
        .where(
            Image.dataset_id == dataset_id,
            Image.status == ImageStatus.READY,
            Image.uncertainty.is_not(None),  # type: ignore[union-attr]
            Image.id.not_in(annotated_q),  # type: ignore[union-attr]
        )
        .order_by(Image.uncertainty.desc())  # type: ignore[union-attr]
        .limit(1)
    )
    best = session.exec(best_q).first()
    return {
        "scored_now": scored,
        "image": _to_out(session, best) if best else None,
    }
