"""Concept and query routes — the open-vocabulary (SAM3) annotation unit.

A `Concept` is a phrase you can ask a model about. A `Query` is that phrase
applied to one image, carrying the completeness flags that make the record
usable as SAM3 training data.

The important rule enforced here: an empty instance list is NOT a negative
example. Only an explicit `verified_absent` status makes it one.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import Session, func, select

from anvil_api.auth import RequireAnyAuthed, RequireLabeler, SessionDep
from anvil_api.models import (
    Annotation,
    Concept,
    Image,
    Project,
    Query,
    QueryStatus,
    utcnow,
)
from anvil_api.schemas import (
    ConceptIn,
    ConceptOut,
    ConceptPatch,
    QueryIn,
    QueryOut,
    QueryPatch,
)

router = APIRouter(tags=["concepts"])


def _get_project(session: Session, project_id: UUID) -> Project:
    p = session.get(Project, project_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _concept_out(session: Session, c: Concept) -> ConceptOut:
    q_count = session.exec(
        select(func.count()).select_from(Query).where(Query.concept_id == c.id)
    ).one()
    i_count = session.exec(
        select(func.count())
        .select_from(Annotation)
        .join(Query, Query.id == Annotation.query_id)
        .where(Query.concept_id == c.id)
    ).one()
    return ConceptOut(
        id=c.id,
        project_id=c.project_id,
        canonical_phrase=c.canonical_phrase,
        aliases=c.aliases or [],
        description=c.description,
        class_id=c.class_id,
        color=c.color,
        created_at=c.created_at,
        query_count=q_count,
        instance_count=i_count,
    )


def _query_out(session: Session, q: Query) -> QueryOut:
    n = session.exec(
        select(func.count()).select_from(Annotation).where(Annotation.query_id == q.id)
    ).one()
    return QueryOut(
        id=q.id,
        image_id=q.image_id,
        concept_id=q.concept_id,
        text=q.text,
        status=q.status,
        is_exhaustive=q.is_exhaustive,
        is_pixel_exhaustive=q.is_pixel_exhaustive,
        model_name=q.model_name,
        model_version_id=q.model_version_id,
        threshold=q.threshold,
        instance_count=n,
        created_at=q.created_at,
        updated_at=q.updated_at,
    )


# --------- Concepts ---------
@router.get("/projects/{project_id}/concepts", response_model=list[ConceptOut])
def list_concepts(
    project_id: UUID, session: SessionDep, _u: RequireAnyAuthed
) -> list[ConceptOut]:
    _get_project(session, project_id)
    rows = session.exec(
        select(Concept)
        .where(Concept.project_id == project_id)
        .order_by(Concept.canonical_phrase)
    ).all()
    return [_concept_out(session, c) for c in rows]


@router.post(
    "/projects/{project_id}/concepts",
    response_model=ConceptOut,
    status_code=status.HTTP_201_CREATED,
)
def create_concept(
    project_id: UUID, body: ConceptIn, session: SessionDep, user: RequireLabeler
) -> ConceptOut:
    _get_project(session, project_id)
    phrase = body.canonical_phrase.strip()
    if not phrase:
        raise HTTPException(400, "canonical_phrase cannot be empty")

    existing = session.exec(
        select(Concept).where(
            Concept.project_id == project_id, Concept.canonical_phrase == phrase
        )
    ).first()
    if existing:
        raise HTTPException(409, f"concept '{phrase}' already exists in this project")

    c = Concept(
        project_id=project_id,
        canonical_phrase=phrase,
        aliases=[a.strip() for a in body.aliases if a.strip()],
        description=body.description,
        class_id=body.class_id,
        color=body.color,
        created_by=user.id,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return _concept_out(session, c)


@router.patch("/concepts/{concept_id}", response_model=ConceptOut)
def patch_concept(
    concept_id: UUID, body: ConceptPatch, session: SessionDep, _u: RequireLabeler
) -> ConceptOut:
    c = session.get(Concept, concept_id)
    if not c:
        raise HTTPException(404, "concept not found")

    if body.canonical_phrase is not None:
        phrase = body.canonical_phrase.strip()
        if not phrase:
            raise HTTPException(400, "canonical_phrase cannot be empty")
        clash = session.exec(
            select(Concept).where(
                Concept.project_id == c.project_id,
                Concept.canonical_phrase == phrase,
                Concept.id != c.id,
            )
        ).first()
        if clash:
            raise HTTPException(409, f"concept '{phrase}' already exists")
        c.canonical_phrase = phrase
    if body.aliases is not None:
        c.aliases = [a.strip() for a in body.aliases if a.strip()]
    if body.description is not None:
        c.description = body.description
    if body.class_id is not None:
        c.class_id = body.class_id
    if body.color is not None:
        c.color = body.color

    session.add(c)
    session.commit()
    session.refresh(c)
    return _concept_out(session, c)


@router.delete("/concepts/{concept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_concept(
    concept_id: UUID, session: SessionDep, _u: RequireLabeler
) -> None:
    c = session.get(Concept, concept_id)
    if not c:
        raise HTTPException(404, "concept not found")

    queries = session.exec(select(Query).where(Query.concept_id == concept_id)).all()
    for q in queries:
        # Detach instances rather than deleting them: the mask is still valid
        # work, it just loses its concept association.
        anns = session.exec(
            select(Annotation).where(Annotation.query_id == q.id)
        ).all()
        for a in anns:
            a.query_id = None
            session.add(a)
        session.delete(q)
    session.delete(c)
    session.commit()


# --------- Queries ---------
@router.get("/images/{image_id}/queries", response_model=list[QueryOut])
def list_queries(
    image_id: UUID, session: SessionDep, _u: RequireAnyAuthed
) -> list[QueryOut]:
    if not session.get(Image, image_id):
        raise HTTPException(404, "image not found")
    rows = session.exec(
        select(Query).where(Query.image_id == image_id).order_by(Query.created_at)
    ).all()
    return [_query_out(session, q) for q in rows]


@router.post(
    "/images/{image_id}/queries",
    response_model=QueryOut,
    status_code=status.HTTP_201_CREATED,
)
def create_query(
    image_id: UUID, body: QueryIn, session: SessionDep, user: RequireLabeler
) -> QueryOut:
    """Create or return the (image, concept) query.

    Idempotent: re-running a concept on an image must not fork its
    exhaustiveness metadata into two competing rows.
    """
    if not session.get(Image, image_id):
        raise HTTPException(404, "image not found")
    concept = session.get(Concept, body.concept_id)
    if not concept:
        raise HTTPException(404, "concept not found")

    existing = session.exec(
        select(Query).where(
            Query.image_id == image_id, Query.concept_id == body.concept_id
        )
    ).first()
    if existing:
        return _query_out(session, existing)

    q = Query(
        image_id=image_id,
        concept_id=body.concept_id,
        text=(body.text or concept.canonical_phrase).strip(),
        status=body.status,
        is_exhaustive=body.is_exhaustive,
        is_pixel_exhaustive=body.is_pixel_exhaustive,
        model_name=body.model_name,
        model_version_id=body.model_version_id,
        threshold=body.threshold,
        created_by=user.id,
    )
    session.add(q)
    session.commit()
    session.refresh(q)
    return _query_out(session, q)


@router.patch("/queries/{query_id}", response_model=QueryOut)
def patch_query(
    query_id: UUID, body: QueryPatch, session: SessionDep, _u: RequireLabeler
) -> QueryOut:
    q = session.get(Query, query_id)
    if not q:
        raise HTTPException(404, "query not found")

    if body.status is not None:
        n = session.exec(
            select(func.count())
            .select_from(Annotation)
            .where(Annotation.query_id == q.id)
        ).one()
        # Guard the negative example: "this concept is absent" is a training
        # signal, so it must not coexist with annotated instances.
        if body.status == QueryStatus.VERIFIED_ABSENT and n > 0:
            raise HTTPException(
                409,
                f"cannot mark as verified_absent: {n} instance(s) are annotated. "
                "Delete them first if the concept is truly absent.",
            )
        if body.status == QueryStatus.VERIFIED_PRESENT and n == 0:
            raise HTTPException(
                409,
                "cannot mark as verified_present with 0 instances; "
                "use verified_absent if the concept is not in this image",
            )
        q.status = body.status

    if body.is_exhaustive is not None:
        q.is_exhaustive = body.is_exhaustive
    if body.is_pixel_exhaustive is not None:
        q.is_pixel_exhaustive = body.is_pixel_exhaustive
    if body.text is not None and body.text.strip():
        q.text = body.text.strip()

    q.updated_at = utcnow()
    session.add(q)
    session.commit()
    session.refresh(q)
    return _query_out(session, q)


@router.delete("/queries/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_query(query_id: UUID, session: SessionDep, _u: RequireLabeler) -> None:
    q = session.get(Query, query_id)
    if not q:
        raise HTTPException(404, "query not found")
    anns = session.exec(select(Annotation).where(Annotation.query_id == q.id)).all()
    for a in anns:
        a.query_id = None
        session.add(a)
    session.delete(q)
    session.commit()
