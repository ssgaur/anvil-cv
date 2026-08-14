from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from anvil_api.auth import (
    RequireAnyAuthed,
    RequireLabeler,
    SessionDep,
)
from anvil_api.geometry import normalize_obb_angle, validate_geometry
from anvil_api.models import (
    Annotation,
    Class,
    ClassProperty,
    ClassPropertyType,
    GeneratedBy,
    GeometryType,
    Image,
    ReviewStatus,
)
from anvil_api.schemas import AnnotationIn, AnnotationOut, AnnotationPatch

router = APIRouter(tags=["annotations"])

# Image states in which annotations may be written. SUBMITTED and APPROVED are
# locked — a reviewer must `reopen` first (which transitions back to in_progress).
_WRITABLE_STATES = {ReviewStatus.NEW, ReviewStatus.IN_PROGRESS, ReviewStatus.REJECTED}


def _get_image(session, image_id: UUID) -> Image:
    img = session.get(Image, image_id)
    if not img:
        raise HTTPException(404, "image not found")
    return img


def _ensure_writable(img: Image) -> None:
    """Block annotation writes when the image is in a frozen review state."""
    if img.review_status not in _WRITABLE_STATES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"image is {img.review_status.value if hasattr(img.review_status, 'value') else img.review_status} "
            "— reviewer must reopen before annotations can be edited",
        )


def _to_out(a: Annotation) -> AnnotationOut:
    return AnnotationOut(
        id=a.id, image_id=a.image_id, class_id=a.class_id,
        geometry_type=a.geometry_type, geometry=a.geometry,
        source=a.source, confidence=a.confidence,
        properties=a.properties or {},
        query_id=a.query_id,
        mask_rle=a.mask_rle,
        generated_by=a.generated_by,
        model_name=a.model_name,
        model_version_id=a.model_version_id,
        model_score=a.model_score,
        human_verified=a.human_verified,
        human_corrected=a.human_corrected,
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
def list_annotations(image_id: UUID, session: SessionDep, _user: RequireAnyAuthed) -> list[AnnotationOut]:
    _get_image(session, image_id)
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
    image_id: UUID, body: AnnotationIn, session: SessionDep, user: RequireLabeler,
) -> AnnotationOut:
    img = _get_image(session, image_id)
    _ensure_writable(img)
    cls = session.get(Class, body.class_id)
    if not cls:
        raise HTTPException(400, "unknown class_id")
    # Centralized geometry validation (per-type shape + finite numeric checks).
    kp_count = (
        len(cls.keypoint_schema.get("keypoints", []))
        if cls.keypoint_schema and isinstance(cls.keypoint_schema, dict)
        else None
    )
    validate_geometry(body.geometry_type, body.geometry, keypoint_count=kp_count)
    geom = dict(body.geometry)
    if body.geometry_type == GeometryType.OBB:
        geom["angle_rad"] = normalize_obb_angle(float(geom["angle_rad"]))
    a = Annotation(
        image_id=img.id, class_id=body.class_id,
        geometry_type=body.geometry_type, geometry=geom,
        source=body.source, confidence=body.confidence,
        properties=_validate_properties(session, body.class_id, body.properties or {}),
        query_id=body.query_id,
        mask_rle=body.mask_rle,
        generated_by=body.generated_by,
        model_name=body.model_name,
        model_version_id=body.model_version_id,
        model_score=body.model_score,
        human_verified=body.human_verified,
        # Preserve the model's untouched output so we can later measure how
        # much human correction a prediction actually needed (Phase 22).
        original_geometry=body.original_geometry or (
            geom if body.generated_by != GeneratedBy.HUMAN else None
        ),
        original_mask_rle=body.original_mask_rle or (
            body.mask_rle if body.generated_by != GeneratedBy.HUMAN else None
        ),
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
def delete_annotation(annotation_id: UUID, session: SessionDep, _user: RequireLabeler) -> None:
    a = session.get(Annotation, annotation_id)
    if not a:
        raise HTTPException(404, "annotation not found")
    img = _get_image(session, a.image_id)
    _ensure_writable(img)
    session.delete(a)
    session.commit()


@router.patch("/annotations/{annotation_id}", response_model=AnnotationOut)
def update_annotation(
    annotation_id: UUID, body: AnnotationPatch, session: SessionDep, _user: RequireLabeler,
) -> AnnotationOut:
    a = session.get(Annotation, annotation_id)
    if not a:
        raise HTTPException(404, "annotation not found")
    img = _get_image(session, a.image_id)
    _ensure_writable(img)
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
        # Re-validate against (possibly updated) geometry_type and class schema.
        cls_for_kp = session.get(Class, a.class_id)
        kp_count = (
            len(cls_for_kp.keypoint_schema.get("keypoints", []))
            if cls_for_kp and cls_for_kp.keypoint_schema and isinstance(cls_for_kp.keypoint_schema, dict)
            else None
        )
        validate_geometry(a.geometry_type, body.geometry, keypoint_count=kp_count)
        geom = dict(body.geometry)
        if a.geometry_type == GeometryType.OBB:
            geom["angle_rad"] = normalize_obb_angle(float(geom["angle_rad"]))
        # A human editing a model-generated mask is the signal that powers
        # AI acceptance/correction metrics, so record it once and keep the
        # model's original geometry intact.
        if a.generated_by != GeneratedBy.HUMAN and geom != a.geometry:
            if a.original_geometry is None:
                a.original_geometry = a.geometry
            a.human_corrected = True
        a.geometry = geom
    if body.properties is not None:
        a.properties = _validate_properties(session, a.class_id, body.properties)
    if body.mask_rle is not None:
        if a.generated_by != GeneratedBy.HUMAN and a.original_mask_rle is None:
            a.original_mask_rle = a.mask_rle
        a.mask_rle = body.mask_rle
    if body.human_verified is not None:
        a.human_verified = body.human_verified
    session.add(a)
    session.commit()
    session.refresh(a)
    return _to_out(a)
