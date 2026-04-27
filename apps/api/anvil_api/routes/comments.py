"""Image comments — spatial pins or general threads for the review flow."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from anvil_api.auth import (
    RequireAnyAuthed,
    RequireLabeler,
    SessionDep,
)
from anvil_api.models import Comment, Image, User
from anvil_api.schemas import CommentIn, CommentOut, CommentPatch

router = APIRouter(tags=["comments"])


def _check_image(session, image_id: UUID) -> Image:
    img = session.get(Image, image_id)
    if not img:
        raise HTTPException(404, "image not found")
    return img


def _to_out(session, c: Comment) -> CommentOut:
    author = session.get(User, c.author_id)
    return CommentOut(
        id=c.id, image_id=c.image_id, author_id=c.author_id,
        author_name=(author.name if author else "unknown"),
        body=c.body, x=c.x, y=c.y, resolved=c.resolved, created_at=c.created_at,
    )


@router.get("/images/{image_id}/comments", response_model=list[CommentOut])
def list_comments(image_id: UUID, session: SessionDep, _user: RequireAnyAuthed) -> list[CommentOut]:
    _check_image(session, image_id)
    rows = session.exec(
        select(Comment).where(Comment.image_id == image_id).order_by(Comment.created_at)
    ).all()
    return [_to_out(session, c) for c in rows]


@router.post(
    "/images/{image_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    image_id: UUID, body: CommentIn, session: SessionDep, user: RequireLabeler,
) -> CommentOut:
    _check_image(session, image_id)
    if (body.x is None) != (body.y is None):
        raise HTTPException(400, "x and y must both be set or both omitted")
    c = Comment(
        image_id=image_id, author_id=user.id,
        body=body.body.strip(), x=body.x, y=body.y, resolved=False,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return _to_out(session, c)


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def update_comment(
    comment_id: UUID, body: CommentPatch, session: SessionDep, _user: RequireLabeler,
) -> CommentOut:
    c = session.get(Comment, comment_id)
    if not c:
        raise HTTPException(404, "comment not found")
    _check_image(session, c.image_id)
    if body.body is not None:
        c.body = body.body.strip()
    if body.resolved is not None:
        c.resolved = body.resolved
    session.add(c)
    session.commit()
    session.refresh(c)
    return _to_out(session, c)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comment(comment_id: UUID, session: SessionDep, _user: RequireLabeler) -> None:
    c = session.get(Comment, comment_id)
    if not c:
        raise HTTPException(404, "comment not found")
    _check_image(session, c.image_id)
    session.delete(c)
    session.commit()
