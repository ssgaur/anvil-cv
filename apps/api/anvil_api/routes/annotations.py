from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from anvil_api.auth import CurrentUser, SessionDep
from anvil_api.models import Annotation, Class, ClassProperty, ClassPropertyType, Dataset, Image, Project, ReviewStatus
from anvil_api.schemas import AnnotationIn, AnnotationOut, AnnotationPatch

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
        source=a.source, confidence=a.confidence,
        properties=a.properties or {},
        created_at=a.created_at,
    )


def _validate_properties(session, class_id: UUID, props: dict) -> dict:
    """Coerce + validate a properties dict against the class's ClassProperty schema.

    Unknown keys are silently dropped (lets clients send partial updates safely).
    Type mismatches raise 400 (e.g. multi value isn't a list).
    """
    if not props:
        return {}
    schema = session.exec(
        select(ClassProperty).where(ClassProperty.class_id == class_id)
    ).all()
    by_id = {str(cp.id): cp for cp in schema}
    cleaned: dict = {}
    for k, v in props.items():
        cp = by_id.get(str(k))
        if not cp:
            continue  # unknown property → drop
        if cp.type == ClassPropertyType.SINGLE:
            if v is None or v == "":
                continue
            if not isinstance(v, str) or v not in cp.options:
                raise HTTPException(400, f"property '{cp.name}' must be one of {cp.options}")
            cleaned[str(cp.id)] = v
        elif cp.type == ClassPropertyType.MULTI:
            if v is None:
                continue
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise HTTPException(400, f"property '{cp.name}' must be a list of strings")
            if len(v) == 0:
                continue  # empty list = clear
            bad = [x for x in v if x not in cp.options]
            if bad:
                raise HTTPException(400, f"property '{cp.name}' has invalid options: {bad}")
            cleaned[str(cp.id)] = v
        elif cp.type == ClassPropertyType.TEXT:
            if v is None or v == "":
                continue
            if not isinstance(v, str):
                raise HTTPException(400, f"property '{cp.name}' must be a string")
            cleaned[str(cp.id)] = v
    return cleaned


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
        source=body.source, confidence=body.confidence,
        properties=_validate_properties(session, body.class_id, body.properties or {}),
        created_by=user.id,
    )
    session.add(a)
    # First annotation on a NEW image bumps it to in_progress
    if img.review_status == ReviewStatus.NEW:
        img.review_status = ReviewStatus.IN_PROGRESS
        session.add(img)
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


@router.patch("/annotations/{annotation_id}", response_model=AnnotationOut)
def update_annotation(
    annotation_id: UUID, body: AnnotationPatch, session: SessionDep, user: CurrentUser,
) -> AnnotationOut:
    a = session.get(Annotation, annotation_id)
    if not a:
        raise HTTPException(404, "annotation not found")
    _ownership(session, a.image_id, user)
    if body.class_id is not None:
        cls = session.get(Class, body.class_id)
        if not cls:
            raise HTTPException(400, "unknown class_id")
        # Class change wipes properties (different schema)
        if body.class_id != a.class_id:
            a.properties = {}
        a.class_id = body.class_id
    if body.geometry_type is not None:
        a.geometry_type = body.geometry_type
    if body.geometry is not None:
        a.geometry = body.geometry
    if body.properties is not None:
        a.properties = _validate_properties(session, a.class_id, body.properties)
    session.add(a)
    session.commit()
    session.refresh(a)
    return _to_out(a)
