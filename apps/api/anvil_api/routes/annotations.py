from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from anvil_api.auth import CurrentUser, SessionDep
from anvil_api.models import Annotation, Class, Dataset, Image, Project
from anvil_api.schemas import AnnotationIn, AnnotationOut

router = APIRouter(tags=["annotations"])


def _ownership(session, image_id: UUID, user) -> Image:
    img = session.get(Image, image_id)
    if not img:
        raise HTTPException(404, "image not found")
    d = session.get(Dataset, img.dataset_id)
    p = session.get(Project, d.project_id) if d else None
    if not p or p.owner_id != user.id:
        raise HTTPException(404, "image not found")
    return img


def _to_out(a: Annotation) -> AnnotationOut:
    return AnnotationOut(
        id=a.id, image_id=a.image_id, class_id=a.class_id,
        geometry_type=a.geometry_type, geometry=a.geometry,
        source=a.source, confidence=a.confidence, created_at=a.created_at,
    )


@router.get("/images/{image_id}/annotations", response_model=list[AnnotationOut])
def list_annotations(image_id: UUID, session: SessionDep, user: CurrentUser) -> list[AnnotationOut]:
    _ownership(session, image_id, user)
    rows = session.exec(
        select(Annotation).where(Annotation.image_id == image_id).order_by(Annotation.created_at)
    ).all()
    return [_to_out(a) for a in rows]


@router.post(
    "/images/{image_id}/annotations",
    response_model=AnnotationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_annotation(
    image_id: UUID, body: AnnotationIn, session: SessionDep, user: CurrentUser
) -> AnnotationOut:
    img = _ownership(session, image_id, user)
    cls = session.get(Class, body.class_id)
    if not cls:
        raise HTTPException(400, "unknown class_id")
    a = Annotation(
        image_id=img.id, class_id=body.class_id,
        geometry_type=body.geometry_type, geometry=body.geometry,
        source=body.source, confidence=body.confidence, created_by=user.id,
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return _to_out(a)


@router.delete("/annotations/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annotation(annotation_id: UUID, session: SessionDep, user: CurrentUser) -> None:
    a = session.get(Annotation, annotation_id)
    if not a:
        raise HTTPException(404, "annotation not found")
    _ownership(session, a.image_id, user)
    session.delete(a)
    session.commit()
