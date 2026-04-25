"""Model-version routes: list / create (= start fine-tune) / get / set-active."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlmodel import Session, select

from anvil_api.auth import CurrentUser, SessionDep
from anvil_api.db import engine
from anvil_api.ml_client import ml_client
from anvil_api.models import (
    Annotation,
    Dataset,
    Image,
    ImageStatus,
    ModelVersion,
    ModelVersionStatus,
    Project,
)
from anvil_api.schemas import ModelVersionIn, ModelVersionOut, SetActiveModelIn
from anvil_api.storage import storage

router = APIRouter(tags=["models"])


def _check_project(session: Session, project_id: UUID, user) -> Project:
    p = session.get(Project, project_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _to_out(mv: ModelVersion) -> ModelVersionOut:
    return ModelVersionOut(
        id=mv.id,
        project_id=mv.project_id,
        name=mv.name,
        base_model=mv.base_model,
        dataset_id=mv.dataset_id,
        status=mv.status,
        progress=mv.progress,
        error=mv.error,
        num_train_images=mv.num_train_images,
        num_train_annotations=mv.num_train_annotations,
        config=mv.config,
        metrics=mv.metrics,
        created_at=mv.created_at,
        completed_at=mv.completed_at,
    )


@router.get("/projects/{project_id}/model-versions", response_model=list[ModelVersionOut])
def list_versions(
    project_id: UUID, session: SessionDep, user: CurrentUser,
) -> list[ModelVersionOut]:
    _check_project(session, project_id, user)
    rows = session.exec(
        select(ModelVersion).where(ModelVersion.project_id == project_id)
        .order_by(ModelVersion.created_at.desc())
    ).all()
    return [_to_out(r) for r in rows]


@router.get("/model-versions/{version_id}", response_model=ModelVersionOut)
def get_version(version_id: UUID, session: SessionDep, user: CurrentUser) -> ModelVersionOut:
    mv = session.get(ModelVersion, version_id)
    if not mv:
        raise HTTPException(404, "model version not found")
    _check_project(session, mv.project_id, user)
    return _to_out(mv)


@router.post(
    "/projects/{project_id}/model-versions",
    response_model=ModelVersionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    project_id: UUID,
    body: ModelVersionIn,
    background: BackgroundTasks,
    session: SessionDep,
    user: CurrentUser,
) -> ModelVersionOut:
    p = _check_project(session, project_id, user)

    d = session.get(Dataset, body.dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(404, "dataset not found in this project")

    # Build the training set: ready images with at least one annotation
    rows = session.exec(
        select(Image, Annotation)
        .join(Annotation, Annotation.image_id == Image.id)
        .where(Image.dataset_id == d.id)
        .where(Image.status == ImageStatus.READY)
        .where(Image.embedding_key.is_not(None))  # type: ignore[union-attr]
    ).all()
    if not rows:
        raise HTTPException(
            400,
            "no trainable annotations: need ready images with embeddings AND annotations",
        )

    image_ids = {img.id for img, _ in rows}
    n_anns = len(rows)

    mv = ModelVersion(
        project_id=p.id,
        name=body.name,
        dataset_id=d.id,
        base_model="sam2.1_hiera_b+",
        config={
            "epochs": body.epochs, "lr": body.lr, "rank": body.rank,
            "alpha": body.alpha, "eval_split": body.eval_split,
        },
        num_train_images=len(image_ids),
        num_train_annotations=n_anns,
        status=ModelVersionStatus.PENDING,
        created_by=user.id,
    )
    session.add(mv)
    session.commit()
    session.refresh(mv)

    background.add_task(_run_training, mv.id)
    return _to_out(mv)


@router.post("/projects/{project_id}/active-model", response_model=ModelVersionOut | None)
def set_active(
    project_id: UUID, body: SetActiveModelIn, session: SessionDep, user: CurrentUser,
) -> ModelVersionOut | None:
    p = _check_project(session, project_id, user)
    if body.model_version_id is None:
        p.active_model_version_id = None
        session.add(p); session.commit()
        return None
    mv = session.get(ModelVersion, body.model_version_id)
    if not mv or mv.project_id != project_id:
        raise HTTPException(404, "model version not found in project")
    if mv.status != ModelVersionStatus.READY:
        raise HTTPException(409, f"model is not ready (status={mv.status})")
    p.active_model_version_id = mv.id
    session.add(p); session.commit()
    return _to_out(mv)


# ---------- Background training ----------
def _run_training(mv_id: UUID) -> None:
    import asyncio
    asyncio.run(_train_async(mv_id))


async def _train_async(mv_id: UUID) -> None:
    with Session(engine) as session:
        mv = session.get(ModelVersion, mv_id)
        if not mv:
            return
        mv.status = ModelVersionStatus.TRAINING
        session.add(mv); session.commit()

        try:
            # Reload data within this fresh loop
            rows = session.exec(
                select(Image, Annotation)
                .join(Annotation, Annotation.image_id == Image.id)
                .where(Image.dataset_id == mv.dataset_id)
                .where(Image.status == ImageStatus.READY)
                .where(Image.embedding_key.is_not(None))  # type: ignore[union-attr]
            ).all()

            # Group annotations by image
            grouped: dict[UUID, dict] = {}
            for img, ann in rows:
                if img.id not in grouped:
                    grouped[img.id] = {
                        "image_path": storage.absolute_path(img.storage_key),
                        "embedding_path": storage.absolute_path(img.embedding_key),
                        "annotations": [],
                    }
                grouped[img.id]["annotations"].append({
                    "geometry_type": ann.geometry_type.value if hasattr(ann.geometry_type, "value") else ann.geometry_type,
                    "geometry": ann.geometry,
                    "image_w": img.width,
                    "image_h": img.height,
                })

            items = list(grouped.values())

            # Adapter path
            adapter_key = f"projects/{mv.project_id}/adapters/{mv.id}.pt"
            adapter_abs = storage.absolute_path(adapter_key)

            cfg = mv.config or {}
            metrics = await ml_client.train(
                items=items,
                adapter_path=adapter_abs,
                epochs=int(cfg.get("epochs", 20)),
                lr=float(cfg.get("lr", 1e-4)),
                rank=int(cfg.get("rank", 8)),
                alpha=int(cfg.get("alpha", 16)),
                eval_split=float(cfg.get("eval_split", 0.2)),
                device="cpu",  # safer than mps for backward on M1
            )
            mv.metrics = metrics
            mv.adapter_key = adapter_key
            mv.progress = 1.0
            mv.status = ModelVersionStatus.READY
            mv.completed_at = datetime.now(timezone.utc)
            mv.error = None
        except Exception as e:
            mv.status = ModelVersionStatus.FAILED
            mv.error = str(e)[:1000]
        session.add(mv); session.commit()
