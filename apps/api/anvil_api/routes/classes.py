from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import func, select

from anvil_api.auth import CurrentUser, SessionDep
from anvil_api.models import Annotation, Class, Project
from anvil_api.schemas import ClassIn, ClassOut

router = APIRouter(prefix="/projects/{project_id}/classes", tags=["classes"])

PALETTE = [
    "#22c55e", "#3b82f6", "#ef4444", "#f59e0b", "#a855f7",
    "#06b6d4", "#ec4899", "#84cc16", "#eab308", "#14b8a6",
    "#f97316", "#8b5cf6", "#10b981", "#0ea5e9", "#f43f5e",
    "#6366f1", "#d946ef", "#22d3ee", "#fb7185", "#facc15",
]


def _check(session, project_id: UUID, user) -> Project:
    p = session.get(Project, project_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _to_out(session, c: Class) -> ClassOut:
    n = session.exec(select(func.count()).select_from(Annotation).where(Annotation.class_id == c.id)).one()
    return ClassOut(
        id=c.id, project_id=c.project_id, name=c.name, color=c.color,
        geometry_type=c.geometry_type, description=c.description, annotation_count=n,
    )


@router.get("", response_model=list[ClassOut])
def list_classes(project_id: UUID, session: SessionDep, user: CurrentUser) -> list[ClassOut]:
    _check(session, project_id, user)
    rows = session.exec(select(Class).where(Class.project_id == project_id).order_by(Class.created_at)).all()
    return [_to_out(session, c) for c in rows]


@router.post("", response_model=ClassOut, status_code=status.HTTP_201_CREATED)
def create_class(project_id: UUID, body: ClassIn, session: SessionDep, user: CurrentUser) -> ClassOut:
    _check(session, project_id, user)
    n_existing = session.exec(
        select(func.count()).select_from(Class).where(Class.project_id == project_id)
    ).one()
    color = body.color or PALETTE[n_existing % len(PALETTE)]
    c = Class(
        project_id=project_id, name=body.name, color=color,
        geometry_type=body.geometry_type, description=body.description,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return _to_out(session, c)


@router.delete("/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_class(project_id: UUID, class_id: UUID, session: SessionDep, user: CurrentUser) -> None:
    _check(session, project_id, user)
    c = session.get(Class, class_id)
    if not c or c.project_id != project_id:
        raise HTTPException(404, "class not found")
    session.delete(c)
    session.commit()
