from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import func, select

from anvil_api.auth import CurrentUser, SessionDep
from anvil_api.models import Dataset, Image, Project
from anvil_api.schemas import ProjectIn, ProjectOut
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
def list_projects(session: SessionDep, user: CurrentUser) -> list[ProjectOut]:
    rows = session.exec(select(Project).where(Project.owner_id == user.id).order_by(Project.created_at.desc())).all()
    return [_to_out(session, p) for p in rows]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectIn, session: SessionDep, user: CurrentUser) -> ProjectOut:
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
def get_project(project_id: UUID, session: SessionDep, user: CurrentUser) -> ProjectOut:
    p = session.get(Project, project_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return _to_out(session, p)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: UUID, session: SessionDep, user: CurrentUser) -> None:
    p = session.get(Project, project_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    session.delete(p)
    session.commit()
