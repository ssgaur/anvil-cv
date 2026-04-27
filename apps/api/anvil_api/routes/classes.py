from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import func, select

from anvil_api.auth import (
    RequireAnyAuthed,
    RequireReviewer,
    SessionDep,
)
from anvil_api.models import Annotation, Class, ClassProperty, Project
from anvil_api.schemas import (
    ClassIn,
    ClassOut,
    ClassPatch,
    ClassPropertyIn,
    ClassPropertyOut,
    ClassPropertyPatch,
)

router = APIRouter(prefix="/projects/{project_id}/classes", tags=["classes"])

PALETTE = [
    # Curated for AA contrast against #0a0a0a (Tailwind neutral-950) backgrounds.
    # Hand-picked to be visually distinct at a glance — reds → ambers → yellows
    # → greens → cyans → blues → purples → pinks. Skips low-saturation greys.
    "#ef4444",  # red-500
    "#f97316",  # orange-500
    "#f59e0b",  # amber-500
    "#eab308",  # yellow-500
    "#84cc16",  # lime-500
    "#22c55e",  # green-500
    "#10b981",  # emerald-500
    "#14b8a6",  # teal-500
    "#06b6d4",  # cyan-500
    "#0ea5e9",  # sky-500
    "#3b82f6",  # blue-500
    "#6366f1",  # indigo-500
    "#8b5cf6",  # violet-500
    "#a855f7",  # purple-500
    "#d946ef",  # fuchsia-500
    "#ec4899",  # pink-500
    "#f43f5e",  # rose-500
    "#fb7185",  # rose-400 (lighter for variety)
    "#facc15",  # yellow-400
    "#22d3ee",  # cyan-400
    "#34d399",  # emerald-400
    "#a78bfa",  # violet-400
    "#fbbf24",  # amber-400
    "#fca5a5",  # red-300
]


def _check(session, project_id: UUID) -> Project:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _to_out(session, c: Class) -> ClassOut:
    n = session.exec(select(func.count()).select_from(Annotation).where(Annotation.class_id == c.id)).one()
    return ClassOut(
        id=c.id, project_id=c.project_id, name=c.name, color=c.color,
        geometry_type=c.geometry_type, description=c.description, annotation_count=n,
    )


@router.get("", response_model=list[ClassOut])
def list_classes(project_id: UUID, session: SessionDep, _user: RequireAnyAuthed) -> list[ClassOut]:
    _check(session, project_id)
    rows = session.exec(select(Class).where(Class.project_id == project_id).order_by(Class.created_at)).all()
    return [_to_out(session, c) for c in rows]


@router.post("", response_model=ClassOut, status_code=status.HTTP_201_CREATED)
def create_class(project_id: UUID, body: ClassIn, session: SessionDep, _r: RequireReviewer) -> ClassOut:
    _check(session, project_id)
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
def delete_class(project_id: UUID, class_id: UUID, session: SessionDep, _r: RequireReviewer) -> None:
    _check(session, project_id)
    c = session.get(Class, class_id)
    if not c or c.project_id != project_id:
        raise HTTPException(404, "class not found")
    session.delete(c)
    session.commit()


@router.patch("/{class_id}", response_model=ClassOut)
def update_class(
    project_id: UUID, class_id: UUID, body: ClassPatch,
    session: SessionDep, _r: RequireReviewer,
) -> ClassOut:
    _check(session, project_id)
    c = session.get(Class, class_id)
    if not c or c.project_id != project_id:
        raise HTTPException(404, "class not found")
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "name cannot be empty")
        c.name = body.name.strip()
    if body.color is not None:
        c.color = body.color
    if body.geometry_type is not None:
        c.geometry_type = body.geometry_type
    if body.description is not None:
        c.description = body.description
    session.add(c)
    session.commit()
    session.refresh(c)
    return _to_out(session, c)


# ---------- Class properties ----------

def _check_class(session, project_id: UUID, class_id: UUID) -> Class:
    _check(session, project_id)
    c = session.get(Class, class_id)
    if not c or c.project_id != project_id:
        raise HTTPException(404, "class not found")
    return c


def _cp_out(cp: ClassProperty) -> ClassPropertyOut:
    return ClassPropertyOut(
        id=cp.id, class_id=cp.class_id, name=cp.name, type=cp.type,
        options=cp.options or [], required=cp.required, sort_order=cp.sort_order,
    )


@router.get("/{class_id}/properties", response_model=list[ClassPropertyOut])
def list_class_properties(
    project_id: UUID, class_id: UUID, session: SessionDep, _user: RequireAnyAuthed,
) -> list[ClassPropertyOut]:
    _check_class(session, project_id, class_id)
    rows = session.exec(
        select(ClassProperty).where(ClassProperty.class_id == class_id)
        .order_by(ClassProperty.sort_order, ClassProperty.created_at)
    ).all()
    return [_cp_out(cp) for cp in rows]


@router.post(
    "/{class_id}/properties",
    response_model=ClassPropertyOut,
    status_code=status.HTTP_201_CREATED,
)
def create_class_property(
    project_id: UUID, class_id: UUID, body: ClassPropertyIn,
    session: SessionDep, _r: RequireReviewer,
) -> ClassPropertyOut:
    _check_class(session, project_id, class_id)
    if not body.name.strip():
        raise HTTPException(400, "name cannot be empty")
    cp = ClassProperty(
        class_id=class_id, name=body.name.strip(), type=body.type,
        options=list(body.options or []),
        required=body.required, sort_order=body.sort_order,
    )
    session.add(cp)
    session.commit()
    session.refresh(cp)
    return _cp_out(cp)


@router.patch(
    "/{class_id}/properties/{property_id}",
    response_model=ClassPropertyOut,
)
def update_class_property(
    project_id: UUID, class_id: UUID, property_id: UUID,
    body: ClassPropertyPatch,
    session: SessionDep, _r: RequireReviewer,
) -> ClassPropertyOut:
    _check_class(session, project_id, class_id)
    cp = session.get(ClassProperty, property_id)
    if not cp or cp.class_id != class_id:
        raise HTTPException(404, "property not found")
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "name cannot be empty")
        cp.name = body.name.strip()
    if body.type is not None:
        cp.type = body.type
    if body.options is not None:
        cp.options = list(body.options)
    if body.required is not None:
        cp.required = body.required
    if body.sort_order is not None:
        cp.sort_order = body.sort_order
    session.add(cp)
    session.commit()
    session.refresh(cp)
    return _cp_out(cp)


@router.delete(
    "/{class_id}/properties/{property_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_class_property(
    project_id: UUID, class_id: UUID, property_id: UUID,
    session: SessionDep, _r: RequireReviewer,
) -> None:
    _check_class(session, project_id, class_id)
    cp = session.get(ClassProperty, property_id)
    if not cp or cp.class_id != class_id:
        raise HTTPException(404, "property not found")
    # Strip this property from any annotations that reference it (best-effort).
    pid = str(cp.id)
    anns = session.exec(
        select(Annotation).where(Annotation.class_id == class_id)
    ).all()
    for a in anns:
        if a.properties and pid in a.properties:
            new_props = {k: v for k, v in a.properties.items() if k != pid}
            a.properties = new_props
            session.add(a)
    session.delete(cp)
    session.commit()
