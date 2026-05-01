import contextlib
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import func, select

from anvil_api.auth import (
    RequireAdmin,
    RequireAnyAuthed,
    SessionDep,
)
from anvil_api.models import (
    Annotation,
    Class,
    ClassProperty,
    Comment,
    Dataset,
    Image,
    ModelVersion,
    ModelVersionStatus,
    Project,
)
from anvil_api.schemas import ProjectIn, ProjectOut
from anvil_api.storage import storage
from anvil_api.utils import unique_slug

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_out(session, p: Project) -> ProjectOut:
    ds_count = session.exec(
        select(func.count()).select_from(Dataset).where(Dataset.project_id == p.id)
    ).one()
    img_count = session.exec(
        select(func.count())
        .select_from(Image)
        .join(Dataset, Image.dataset_id == Dataset.id)
        .where(Dataset.project_id == p.id)
    ).one()
    return ProjectOut(
        id=p.id, name=p.name, slug=p.slug, description=p.description,
        owner_id=p.owner_id, created_at=p.created_at,
        dataset_count=ds_count, image_count=img_count,
    )


@router.get("", response_model=list[ProjectOut])
def list_projects(session: SessionDep, _user: RequireAnyAuthed) -> list[ProjectOut]:
    rows = session.exec(select(Project).order_by(Project.created_at.desc())).all()
    return [_to_out(session, p) for p in rows]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectIn, session: SessionDep, user: RequireAdmin) -> ProjectOut:
    existing_slugs = set(session.exec(select(Project.slug)).all())
    p = Project(
        name=body.name,
        slug=unique_slug(body.name, existing_slugs),
        description=body.description,
        owner_id=user.id,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return _to_out(session, p)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: UUID, session: SessionDep, _user: RequireAnyAuthed) -> ProjectOut:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return _to_out(session, p)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: UUID, session: SessionDep, _admin: RequireAdmin) -> None:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    model_versions = list(
        session.exec(select(ModelVersion).where(ModelVersion.project_id == project_id)).all()
    )
    if any(mv.status == ModelVersionStatus.TRAINING for mv in model_versions):
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot delete project while a model version is training")

    datasets = list(session.exec(select(Dataset).where(Dataset.project_id == project_id)).all())
    dataset_ids = [d.id for d in datasets]
    images: list[Image] = []
    if dataset_ids:
        images = list(session.exec(select(Image).where(Image.dataset_id.in_(dataset_ids))).all())  # type: ignore[union-attr]
    image_ids = [im.id for im in images]

    classes = list(session.exec(select(Class).where(Class.project_id == project_id)).all())
    class_ids = [c.id for c in classes]

    # Storage cleanup is best-effort; DB cleanup should still complete if a file
    # has already been removed or is temporarily unavailable.
    for im in images:
        for key in (im.storage_key, im.thumbnail_key, im.embedding_key):
            if key:
                with contextlib.suppress(Exception):
                    storage.delete(key)
    for mv in model_versions:
        if mv.adapter_key:
            with contextlib.suppress(Exception):
                storage.delete(mv.adapter_key)

    p.active_model_version_id = None
    session.add(p)
    session.flush()

    for mv in model_versions:
        session.delete(mv)

    if image_ids:
        for comment in session.exec(select(Comment).where(Comment.image_id.in_(image_ids))).all():  # type: ignore[union-attr]
            session.delete(comment)
        for annotation in session.exec(select(Annotation).where(Annotation.image_id.in_(image_ids))).all():  # type: ignore[union-attr]
            session.delete(annotation)

    if class_ids:
        for prop in session.exec(select(ClassProperty).where(ClassProperty.class_id.in_(class_ids))).all():  # type: ignore[union-attr]
            session.delete(prop)
        for cls in classes:
            cls.parent_id = None
            session.add(cls)
        session.flush()

    for im in images:
        session.delete(im)
    for d in datasets:
        session.delete(d)
    for cls in classes:
        session.delete(cls)
    session.delete(p)
    session.commit()
