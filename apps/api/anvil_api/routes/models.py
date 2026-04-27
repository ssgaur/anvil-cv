"""Model-version routes: list / create (= start fine-tune) / get / set-active."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlmodel import Session, select

from anvil_api.auth import (
    RequireAdmin,
    RequireAnyAuthed,
    RequireReviewer,
    SessionDep,
)
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
from anvil_api.schemas import CompareIn, ModelVersionIn, ModelVersionOut, SetActiveModelIn
from anvil_api.storage import storage

router = APIRouter(tags=["models"])


def _check_project(session: Session, project_id: UUID) -> Project:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _to_out(mv: ModelVersion, *, is_active: bool = False) -> ModelVersionOut:
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
        is_active=is_active,
    )


@router.get("/projects/{project_id}/model-versions", response_model=list[ModelVersionOut])
def list_versions(
    project_id: UUID, session: SessionDep, _user: RequireAnyAuthed,
) -> list[ModelVersionOut]:
    p = _check_project(session, project_id)
    rows = session.exec(
        select(ModelVersion).where(ModelVersion.project_id == project_id)
        .order_by(ModelVersion.created_at.desc())
    ).all()
    return [_to_out(r, is_active=(p.active_model_version_id == r.id)) for r in rows]


@router.get("/model-versions/{version_id}", response_model=ModelVersionOut)
def get_version(version_id: UUID, session: SessionDep, _user: RequireAnyAuthed) -> ModelVersionOut:
    mv = session.get(ModelVersion, version_id)
    if not mv:
        raise HTTPException(404, "model version not found")
    p = _check_project(session, mv.project_id)
    return _to_out(mv, is_active=(p.active_model_version_id == mv.id))


@router.delete("/model-versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_version(version_id: UUID, session: SessionDep, _admin: RequireAdmin) -> None:
    mv = session.get(ModelVersion, version_id)
    if not mv:
        raise HTTPException(404, "model version not found")
    p = _check_project(session, mv.project_id)
    if mv.status == ModelVersionStatus.TRAINING:
        raise HTTPException(409, "cannot delete a model version that is currently training")
    if p.active_model_version_id == mv.id:
        p.active_model_version_id = None
        session.add(p)
    if mv.adapter_key:
        try:
            ap = storage.absolute_path(mv.adapter_key)
            from pathlib import Path as _P
            _P(ap).unlink(missing_ok=True)
        except Exception:
            pass
    session.delete(mv)
    session.commit()


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
    user: RequireAdmin,
) -> ModelVersionOut:
    p = _check_project(session, project_id)

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
    project_id: UUID, body: SetActiveModelIn, session: SessionDep, _r: RequireReviewer,
) -> ModelVersionOut | None:
    p = _check_project(session, project_id)
    new_mv_id = None if body.model_version_id is None else body.model_version_id
    if new_mv_id is not None:
        mv = session.get(ModelVersion, new_mv_id)
        if not mv or mv.project_id != project_id:
            raise HTTPException(404, "model version not found in project")
        if mv.status != ModelVersionStatus.READY:
            raise HTTPException(409, f"model is not ready (status={mv.status})")
    else:
        mv = None

    if p.active_model_version_id != new_mv_id:
        p.active_model_version_id = new_mv_id
        session.add(p)
        # Invalidate uncertainty scores for all images in this project's datasets,
        # since they were computed against the previous active model.
        from anvil_api.models import Dataset, Image
        ds_ids = session.exec(select(Dataset.id).where(Dataset.project_id == project_id)).all()
        if ds_ids:
            imgs = session.exec(
                select(Image).where(Image.dataset_id.in_(ds_ids))  # type: ignore[union-attr]
            ).all()
            for img in imgs:
                img.uncertainty = None
                session.add(img)
        session.commit()
    return _to_out(mv, is_active=True) if mv else None


# ---------- A/B compare ----------
@router.post("/projects/{project_id}/model-versions/compare")
async def compare_versions(
    project_id: UUID,
    body: CompareIn,
    session: SessionDep,
    _r: RequireReviewer,
) -> dict:
    """Re-evaluate two model versions on the SAME randomly-sampled held-out set
    and return per-class IoU + deltas.  Either side may be null = base SAM2."""
    _check_project(session, project_id)

    def _resolve(mv_id: UUID | None) -> tuple[str | None, str]:
        if mv_id is None:
            return None, "Base SAM2"
        mv = session.get(ModelVersion, mv_id)
        if not mv or mv.project_id != project_id:
            raise HTTPException(404, f"model version {mv_id} not in project")
        if mv.status != ModelVersionStatus.READY:
            raise HTTPException(409, f"model version {mv_id} is not ready (status={mv.status})")
        return (storage.absolute_path(mv.adapter_key) if mv.adapter_key else None, mv.name)

    a_adapter, a_name = _resolve(body.a_id)
    b_adapter, b_name = _resolve(body.b_id)

    # Build the eval pool: ready images with annotations from this project's datasets.
    # Prefer dataset_id if given; otherwise pull from the entire project.
    from anvil_api.models import Class as ClassModel
    q = (
        select(Image, Annotation, ClassModel)
        .join(Annotation, Annotation.image_id == Image.id)
        .join(ClassModel, ClassModel.id == Annotation.class_id)
        .join(Dataset, Dataset.id == Image.dataset_id)
        .where(Dataset.project_id == project_id)
        .where(Image.status == ImageStatus.READY)
        .where(Image.embedding_key.is_not(None))  # type: ignore[union-attr]
    )
    if body.dataset_id:
        q = q.where(Image.dataset_id == body.dataset_id)
    rows = session.exec(q).all()
    if not rows:
        raise HTTPException(400, "no annotated/ready images available for compare")

    # Reservoir-sample N
    import random as _r
    rng = _r.Random(body.seed if body.seed is not None else 7)
    rng.shuffle(rows)
    sample = rows[: max(1, body.n_samples)]

    grouped: dict[UUID, dict] = {}
    for img, ann, cls in sample:
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
            "class_id": str(cls.id),
            "class_name": cls.name,
        })
    items = list(grouped.values())
    n_anns = sum(len(g["annotations"]) for g in items)

    a = await ml_client.evaluate(items=items, adapter_path=a_adapter, device="cpu")
    b = await ml_client.evaluate(items=items, adapter_path=b_adapter, device="cpu")

    # Build deltas keyed by class name
    classes = sorted(set(a["per_class_iou"].keys()) | set(b["per_class_iou"].keys()))
    per_class = []
    for cn in classes:
        ai = a["per_class_iou"].get(cn)
        bi = b["per_class_iou"].get(cn)
        per_class.append({
            "class_name": cn,
            "a_iou": ai,
            "b_iou": bi,
            "delta": (None if (ai is None or bi is None) else round(bi - ai, 4)),
        })

    # Per-image join (same image_path across the two runs)
    by_path_a = {it["image_path"] + "|" + it["class_name"]: it["iou"] for it in a["per_item"]}
    by_path_b = {it["image_path"] + "|" + it["class_name"]: it["iou"] for it in b["per_item"]}
    per_item = []
    for k in sorted(set(by_path_a.keys()) | set(by_path_b.keys())):
        path, cname = k.rsplit("|", 1)
        per_item.append({
            "image_path": path.split("/")[-1],
            "class_name": cname,
            "a_iou": by_path_a.get(k),
            "b_iou": by_path_b.get(k),
        })

    return {
        "a": {"id": str(body.a_id) if body.a_id else None, "name": a_name,
              "mean_iou": round(a["mean_iou"], 4), "per_class_iou": a["per_class_iou"]},
        "b": {"id": str(body.b_id) if body.b_id else None, "name": b_name,
              "mean_iou": round(b["mean_iou"], 4), "per_class_iou": b["per_class_iou"]},
        "delta_mean_iou": round(b["mean_iou"] - a["mean_iou"], 4),
        "per_class": per_class,
        "per_item": per_item,
        "n_samples_requested": body.n_samples,
        "n_annotations_evaluated": n_anns,
    }


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
            # Reload data within this fresh loop, joined with class for naming
            from anvil_api.models import Class as ClassModel
            rows = session.exec(
                select(Image, Annotation, ClassModel)
                .join(Annotation, Annotation.image_id == Image.id)
                .join(ClassModel, ClassModel.id == Annotation.class_id)
                .where(Image.dataset_id == mv.dataset_id)
                .where(Image.status == ImageStatus.READY)
                .where(Image.embedding_key.is_not(None))  # type: ignore[union-attr]
            ).all()

            # Group annotations by image
            grouped: dict[UUID, dict] = {}
            for img, ann, cls in rows:
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
                    "class_id": str(cls.id),
                    "class_name": cls.name,
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
            mv.completed_at = datetime.now(UTC)
            mv.error = None
        except Exception as e:
            mv.status = ModelVersionStatus.FAILED
            mv.error = str(e)[:1000]
        session.add(mv); session.commit()
