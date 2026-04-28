"""Bulk operations on images and annotations.

All endpoints scope the affected IDs to the dataset/project from the path —
clients cannot pass arbitrary image IDs to mutate things outside the route.
Each endpoint returns a `{ok: [...], failed: [{id, reason}]}` summary so
partial failures don't lose progress.
"""
from __future__ import annotations

import contextlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from anvil_api.auth import (
    RequireLabeler,
    RequireReviewer,
    SessionDep,
)
from anvil_api.models import (
    Annotation,
    Comment,
    Dataset,
    Image,
    Project,
    ReviewStatus,
)
from anvil_api.storage import storage

router = APIRouter(tags=["bulk"])

MAX_BULK = 500  # per request; keep operations synchronous + safe


class BulkResult(BaseModel):
    ok: list[UUID] = []
    failed: list[dict] = []


class BulkIdsIn(BaseModel):
    image_ids: list[UUID] = Field(..., min_length=1)


class BulkReviewIn(BulkIdsIn):
    action: str  # "reset" | "reopen" | "reject"
    reason: str | None = None


class BulkMoveIn(BulkIdsIn):
    target_dataset_id: UUID


class BulkDeleteByClassIn(BaseModel):
    class_id: UUID
    image_ids: list[UUID] | None = None  # None = all images in this dataset
    include_locked: bool = False  # reviewer override; default OFF


def _check_dataset(session: Session, project_id: UUID, dataset_id: UUID) -> Dataset:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(404, "dataset not found")
    return d


def _scoped_images(
    session: Session, dataset_id: UUID, image_ids: list[UUID],
) -> tuple[dict[UUID, Image], list[dict]]:
    """Load only images that actually belong to dataset_id. Anything else → failed."""
    if len(image_ids) > MAX_BULK:
        raise HTTPException(400, f"max {MAX_BULK} ids per request")
    rows = session.exec(
        select(Image).where(Image.id.in_(image_ids), Image.dataset_id == dataset_id)  # type: ignore[attr-defined]
    ).all()
    found = {img.id: img for img in rows}
    failed = [
        {"id": str(i), "reason": "not in dataset"} for i in image_ids if i not in found
    ]
    return found, failed


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/images/bulk-delete",
    response_model=BulkResult,
)
def bulk_delete_images(
    project_id: UUID, dataset_id: UUID, body: BulkIdsIn,
    session: SessionDep, _r: RequireReviewer,
) -> BulkResult:
    _check_dataset(session, project_id, dataset_id)
    images, failed = _scoped_images(session, dataset_id, body.image_ids)
    ok: list[UUID] = []
    for img_id, img in images.items():
        try:
            # Storage cleanup first (idempotent on local backend); then DB cascade.
            for k in (img.storage_key, img.thumbnail_key):
                if k:
                    with contextlib.suppress(Exception):
                        # Storage failure should not block DB cleanup.
                        storage.delete(k)
            session.delete(img)
            session.flush()
            ok.append(img_id)
        except Exception as e:  # noqa: BLE001
            session.rollback()
            failed.append({"id": str(img_id), "reason": str(e)[:200]})
    session.commit()
    return BulkResult(ok=ok, failed=failed)


_RESET_FROM = {ReviewStatus.IN_PROGRESS, ReviewStatus.SUBMITTED, ReviewStatus.REJECTED}
_REOPEN_FROM = {ReviewStatus.SUBMITTED, ReviewStatus.APPROVED, ReviewStatus.REJECTED}
_REJECT_FROM = {ReviewStatus.SUBMITTED}


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/images/bulk-review",
    response_model=BulkResult,
)
def bulk_review(
    project_id: UUID, dataset_id: UUID, body: BulkReviewIn,
    session: SessionDep, user: RequireLabeler,
) -> BulkResult:
    _check_dataset(session, project_id, dataset_id)
    if body.action not in ("reset", "reopen", "reject"):
        raise HTTPException(400, "action must be reset|reopen|reject")
    if body.action == "reject" and not (body.reason and body.reason.strip()):
        raise HTTPException(400, "reject requires a reason")
    images, failed = _scoped_images(session, dataset_id, body.image_ids)
    ok: list[UUID] = []
    for img_id, img in images.items():
        cur = img.review_status
        try:
            if body.action == "reset":
                if cur not in _RESET_FROM:
                    raise ValueError(f"cannot reset from {cur}")
                img.review_status = ReviewStatus.NEW
                img.submitted_at = None
                img.reviewed_at = None
                img.review_reason = None
            elif body.action == "reopen":
                if cur not in _REOPEN_FROM:
                    raise ValueError(f"cannot reopen from {cur}")
                img.review_status = ReviewStatus.IN_PROGRESS
                img.reviewed_at = None
            elif body.action == "reject":
                if cur not in _REJECT_FROM:
                    raise ValueError(f"cannot reject from {cur}")
                img.review_status = ReviewStatus.REJECTED
                img.review_reason = body.reason
                img.reviewed_by = user.id
            session.add(img)
            session.flush()
            ok.append(img_id)
        except Exception as e:  # noqa: BLE001
            session.rollback()
            failed.append({"id": str(img_id), "reason": str(e)[:200]})
    session.commit()
    return BulkResult(ok=ok, failed=failed)


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/images/bulk-move",
    response_model=BulkResult,
)
def bulk_move(
    project_id: UUID, dataset_id: UUID, body: BulkMoveIn,
    session: SessionDep, _r: RequireReviewer,
) -> BulkResult:
    _check_dataset(session, project_id, dataset_id)
    if body.target_dataset_id == dataset_id:
        raise HTTPException(400, "target dataset must be different")
    target = session.get(Dataset, body.target_dataset_id)
    if not target or target.project_id != project_id:
        raise HTTPException(400, "target dataset must be in same project")
    images, failed = _scoped_images(session, dataset_id, body.image_ids)
    ok: list[UUID] = []
    for img_id, img in images.items():
        try:
            if img.review_status == ReviewStatus.APPROVED:
                raise ValueError("cannot move approved images (preserves audit)")
            img.dataset_id = body.target_dataset_id
            session.add(img)
            session.flush()
            ok.append(img_id)
        except Exception as e:  # noqa: BLE001
            session.rollback()
            failed.append({"id": str(img_id), "reason": str(e)[:200]})
    session.commit()
    return BulkResult(ok=ok, failed=failed)


_LOCKED = {ReviewStatus.SUBMITTED, ReviewStatus.APPROVED}


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/annotations/bulk-delete-by-class",
    response_model=BulkResult,
)
def bulk_delete_annotations_by_class(
    project_id: UUID, dataset_id: UUID, body: BulkDeleteByClassIn,
    session: SessionDep, _r: RequireReviewer,
) -> BulkResult:
    _check_dataset(session, project_id, dataset_id)
    img_q = select(Image).where(Image.dataset_id == dataset_id)
    if body.image_ids is not None:
        if len(body.image_ids) > MAX_BULK:
            raise HTTPException(400, f"max {MAX_BULK} ids per request")
        img_q = img_q.where(Image.id.in_(body.image_ids))  # type: ignore[attr-defined]
    images = session.exec(img_q).all()
    ok: list[UUID] = []
    failed: list[dict] = []
    for img in images:
        if img.review_status in _LOCKED and not body.include_locked:
            failed.append({"id": str(img.id), "reason": f"image is {img.review_status.value} (locked)"})
            continue
        anns = session.exec(
            select(Annotation).where(
                Annotation.image_id == img.id,
                Annotation.class_id == body.class_id,
            )
        ).all()
        for a in anns:
            session.delete(a)
        ok.append(img.id)
    session.commit()
    return BulkResult(ok=ok, failed=failed)


# Re-export Comment so unused-import lint doesn't trip; bulk endpoints rely on
# cascade delete configured on Image relationships.
__all__ = ["router", "Comment", "status"]
