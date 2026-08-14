"""Dataset versioning — immutable snapshots for reproducible training.

A model trained "on dataset X" is unreproducible the moment someone fixes a
mask. Freezing the exact image/annotation/query IDs (plus the train/val/test
assignment) means a training run can always be replayed on the same data.

Snapshots are append-only: corrections produce a new version rather than
mutating an existing one.

Note: membership is deliberately NOT gated on `ImageStatus.READY`. READY means
"SAM2 embedding succeeded", which is an inference optimisation — SAM3 training
needs no SAM2 embeddings, and an image whose embedding failed still has valid
pixels and annotations. The human quality gate is `review_status`; the image
status breakdown is reported in stats for transparency.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from anvil_api.auth import RequireAnyAuthed, RequireLabeler, SessionDep
from anvil_api.models import (
    Annotation,
    Concept,
    Dataset,
    DatasetVersion,
    Image,
    Query,
    QueryStatus,
)
from anvil_api.schemas import DatasetVersionIn, DatasetVersionOut

router = APIRouter(tags=["dataset-versions"])


def _out(v: DatasetVersion) -> DatasetVersionOut:
    return DatasetVersionOut(
        id=v.id,
        dataset_id=v.dataset_id,
        version=v.version,
        name=v.name,
        description=v.description,
        stats=v.stats or {},
        splits=v.splits or {},
        frozen=v.frozen,
        created_at=v.created_at,
    )


def _assign_split(image_id: UUID, seed: int, train: float, val: float) -> str:
    """Deterministic per-image split.

    Hash-based rather than random.shuffle so the same image always lands in the
    same split for a given seed, even if the dataset grows between versions.
    That keeps val/test honest as data is added.
    """
    h = hashlib.sha256(f"{seed}:{image_id}".encode()).digest()
    bucket = int.from_bytes(h[:8], "big") / float(1 << 64)
    if bucket < train:
        return "train"
    if bucket < train + val:
        return "val"
    return "test"


@router.get(
    "/projects/{project_id}/datasets/{dataset_id}/versions",
    response_model=list[DatasetVersionOut],
)
def list_versions(
    project_id: UUID, dataset_id: UUID, session: SessionDep, _u: RequireAnyAuthed
) -> list[DatasetVersionOut]:
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(404, "dataset not found")
    rows = session.exec(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version.desc())  # type: ignore[union-attr]
    ).all()
    return [_out(v) for v in rows]


@router.get("/dataset-versions/{version_id}", response_model=DatasetVersionOut)
def get_version(
    version_id: UUID, session: SessionDep, _u: RequireAnyAuthed
) -> DatasetVersionOut:
    v = session.get(DatasetVersion, version_id)
    if not v:
        raise HTTPException(404, "dataset version not found")
    return _out(v)


@router.post(
    "/projects/{project_id}/datasets/{dataset_id}/versions",
    response_model=DatasetVersionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    project_id: UUID,
    dataset_id: UUID,
    body: DatasetVersionIn,
    session: SessionDep,
    user: RequireLabeler,
) -> DatasetVersionOut:
    """Freeze the current dataset state into a new immutable version."""
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(404, "dataset not found")

    if body.train_split + body.val_split > 1.0:
        raise HTTPException(400, "train_split + val_split must be <= 1.0")

    img_q = select(Image).where(Image.dataset_id == dataset_id)
    if body.review_status is not None:
        img_q = img_q.where(Image.review_status == body.review_status)
    images = session.exec(img_q).all()
    if not images:
        raise HTTPException(
            400,
            "no eligible images in this dataset"
            + (
                f" with review_status='{body.review_status.value}'"
                if body.review_status
                else ""
            ),
        )

    image_ids = [i.id for i in images]
    annotations = session.exec(
        select(Annotation).where(Annotation.image_id.in_(image_ids))  # type: ignore[union-attr]
    ).all()
    queries = session.exec(
        select(Query).where(Query.image_id.in_(image_ids))  # type: ignore[union-attr]
    ).all()
    concept_ids = sorted({str(q.concept_id) for q in queries})
    concepts = (
        session.exec(
            select(Concept).where(Concept.id.in_([UUID(c) for c in concept_ids]))  # type: ignore[union-attr]
        ).all()
        if concept_ids
        else []
    )

    splits = {
        str(i.id): _assign_split(i.id, body.seed, body.train_split, body.val_split)
        for i in images
    }

    by_class = Counter(str(a.class_id) for a in annotations)
    by_generated = Counter(str(a.generated_by) for a in annotations)
    by_geometry = Counter(str(a.geometry_type) for a in annotations)
    by_image_status = Counter(str(i.status) for i in images)
    split_counts = Counter(splits.values())
    positive_q = sum(1 for q in queries if q.status == QueryStatus.VERIFIED_PRESENT)
    negative_q = sum(1 for q in queries if q.status == QueryStatus.VERIFIED_ABSENT)

    stats = {
        "images": len(images),
        "annotations": len(annotations),
        "annotations_with_mask_rle": sum(1 for a in annotations if a.mask_rle),
        "queries": len(queries),
        "queries_verified_present": positive_q,
        "queries_verified_absent": negative_q,
        "queries_exhaustive": sum(1 for q in queries if q.is_exhaustive),
        "queries_pixel_exhaustive": sum(1 for q in queries if q.is_pixel_exhaustive),
        "concepts": len(concepts),
        "by_class": dict(by_class),
        "by_generated_by": dict(by_generated),
        "by_geometry_type": dict(by_geometry),
        "by_image_status": dict(by_image_status),
        "images_without_annotations": sum(
            1 for i in images if not any(a.image_id == i.id for a in annotations)
        ),
        "split_counts": dict(split_counts),
        "review_status_filter": body.review_status.value if body.review_status else None,
    }

    last = session.exec(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version.desc())  # type: ignore[union-attr]
    ).first()
    next_version = (last.version + 1) if last else 1

    v = DatasetVersion(
        dataset_id=dataset_id,
        version=next_version,
        name=body.name or f"v{next_version}",
        description=body.description,
        snapshot={
            "image_ids": [str(i) for i in image_ids],
            "annotation_ids": [str(a.id) for a in annotations],
            "query_ids": [str(q.id) for q in queries],
            "concept_ids": concept_ids,
            "seed": body.seed,
            "train_split": body.train_split,
            "val_split": body.val_split,
        },
        stats=stats,
        splits=splits,
        frozen=True,
        created_by=user.id,
    )
    session.add(v)
    session.commit()
    session.refresh(v)
    return _out(v)


@router.delete(
    "/dataset-versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_version(
    version_id: UUID, session: SessionDep, _u: RequireLabeler
) -> None:
    v = session.get(DatasetVersion, version_id)
    if not v:
        raise HTTPException(404, "dataset version not found")
    session.delete(v)
    session.commit()
