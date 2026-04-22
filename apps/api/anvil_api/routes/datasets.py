from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import func, select

from anvil_api.auth import CurrentUser, SessionDep
from anvil_api.models import Dataset, Image, ImageStatus, Project
from anvil_api.schemas import DatasetIn, DatasetOut
from anvil_api.utils import unique_slug

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


def _check(session, project_id: UUID, user) -> Project:
    p = session.get(Project, project_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _to_out(session, d: Dataset) -> DatasetOut:
    img_count = session.exec(
        select(func.count()).select_from(Image).where(Image.dataset_id == d.id)
    ).one()
    ready_count = session.exec(
        select(func.count())
        .select_from(Image)
        .where(Image.dataset_id == d.id, Image.status == ImageStatus.READY)
    ).one()
    return DatasetOut(
        id=d.id, project_id=d.project_id, name=d.name, slug=d.slug,
        created_at=d.created_at, image_count=img_count, ready_count=ready_count,
    )


@router.get("", response_model=list[DatasetOut])
def list_datasets(project_id: UUID, session: SessionDep, user: CurrentUser) -> list[DatasetOut]:
    _check(session, project_id, user)
    rows = session.exec(
        select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.created_at.desc())
    ).all()
    return [_to_out(session, d) for d in rows]


@router.post("", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
def create_dataset(project_id: UUID, body: DatasetIn, session: SessionDep, user: CurrentUser) -> DatasetOut:
    _check(session, project_id, user)
    existing = set(session.exec(
        select(Dataset.slug).where(Dataset.project_id == project_id)
    ).all())
    d = Dataset(project_id=project_id, name=body.name, slug=unique_slug(body.name, existing))
    session.add(d)
    session.commit()
    session.refresh(d)
    return _to_out(session, d)


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(project_id: UUID, dataset_id: UUID, session: SessionDep, user: CurrentUser) -> DatasetOut:
    _check(session, project_id, user)
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    return _to_out(session, d)


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(project_id: UUID, dataset_id: UUID, session: SessionDep, user: CurrentUser) -> None:
    _check(session, project_id, user)
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    session.delete(d)
    session.commit()
