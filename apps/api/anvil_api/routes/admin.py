"""Admin user-management endpoints (`/admin/users`).

Admin-only. Supports listing users, creating users, patching role/name/password,
and hard-deleting users that have no foreign-key references.

Guards:
  * Cannot delete or demote yourself.
  * Cannot delete or demote the last admin (checked under `SELECT FOR UPDATE`
    to prevent two concurrent admins from both passing the count check).
  * Hard delete is refused when the user owns projects, has authored
    annotations/comments, or created model versions. Operators must reassign
    or delete those rows first. (No soft-delete column yet.)
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlmodel import func, select

from anvil_api.auth import RequireAdmin, SessionDep, hash_password, normalize_email
from anvil_api.models import (
    Annotation,
    Comment,
    ModelVersion,
    Project,
    User,
    UserOAuthIdentity,
    UserRole,
)
from anvil_api.schemas import AdminUserCreateIn, AdminUserOut, AdminUserPatch

router = APIRouter(prefix="/admin/users", tags=["admin"])


def _to_out(session, u: User) -> AdminUserOut:
    providers = session.exec(
        select(UserOAuthIdentity.provider).where(UserOAuthIdentity.user_id == u.id)
    ).all()
    return AdminUserOut(
        id=u.id,
        email=u.email,
        name=u.name,
        role=u.role.value if hasattr(u.role, "value") else str(u.role),
        created_at=u.created_at,
        has_password=bool(u.password_hash),
        oauth_providers=sorted(set(providers)),
    )


def _parse_role(value: str) -> UserRole:
    try:
        return UserRole(value.lower())
    except ValueError as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid role '{value}'; must be one of: "
            + ", ".join(r.value for r in UserRole),
        ) from e


def _lock_admins(session) -> int:
    """Lock all admin user rows for update and return the current admin count.

    Use inside the same transaction as a demote/delete to make the
    "last admin" check race-free.
    """
    session.exec(
        text("SELECT id FROM users WHERE role='admin' FOR UPDATE")
    ).all()
    return session.exec(
        select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
    ).one()


def _user_has_refs(session, user_id: UUID) -> dict[str, int]:
    """Count FK references that would be orphaned by hard-deleting this user."""
    return {
        "projects": session.exec(
            select(func.count()).select_from(Project).where(Project.owner_id == user_id)
        ).one(),
        "annotations": session.exec(
            select(func.count()).select_from(Annotation).where(Annotation.created_by == user_id)
        ).one(),
        "comments": session.exec(
            select(func.count()).select_from(Comment).where(Comment.author_id == user_id)
        ).one(),
        "model_versions": session.exec(
            select(func.count()).select_from(ModelVersion).where(ModelVersion.created_by == user_id)
        ).one(),
    }


@router.get("", response_model=list[AdminUserOut])
def list_users(session: SessionDep, _admin: RequireAdmin) -> list[AdminUserOut]:
    rows = session.exec(select(User).order_by(User.created_at)).all()
    return [_to_out(session, u) for u in rows]


@router.post("", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: AdminUserCreateIn, session: SessionDep, _admin: RequireAdmin,
) -> AdminUserOut:
    role = _parse_role(body.role)
    email = normalize_email(body.email)
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    u = User(
        email=email,
        name=body.name,
        password_hash=hash_password(body.password) if body.password else None,
        role=role,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return _to_out(session, u)


@router.get("/{user_id}", response_model=AdminUserOut)
def get_user(user_id: UUID, session: SessionDep, _admin: RequireAdmin) -> AdminUserOut:
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    return _to_out(session, u)


@router.patch("/{user_id}", response_model=AdminUserOut)
def patch_user(
    user_id: UUID, body: AdminUserPatch, session: SessionDep, admin: RequireAdmin,
) -> AdminUserOut:
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")

    new_role = _parse_role(body.role) if body.role is not None else None

    # Last-admin guard for demotion (under row lock).
    if new_role is not None and u.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
        if u.id == admin.id:
            raise HTTPException(409, "cannot demote yourself")
        n_admins = _lock_admins(session)
        if n_admins <= 1:
            raise HTTPException(409, "cannot demote the last admin")

    if body.name is not None:
        u.name = body.name
    if new_role is not None:
        u.role = new_role
    if body.password is not None:
        u.password_hash = hash_password(body.password)

    session.add(u)
    session.commit()
    session.refresh(u)
    return _to_out(session, u)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: UUID, session: SessionDep, admin: RequireAdmin) -> None:
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if u.id == admin.id:
        raise HTTPException(409, "cannot delete yourself")
    if u.role == UserRole.ADMIN:
        n_admins = _lock_admins(session)
        if n_admins <= 1:
            raise HTTPException(409, "cannot delete the last admin")
    refs = _user_has_refs(session, user_id)
    if any(v > 0 for v in refs.values()):
        raise HTTPException(
            409,
            "user has references that would be orphaned: "
            + ", ".join(f"{k}={v}" for k, v in refs.items() if v > 0)
            + " — reassign or delete those first",
        )
    # OAuth identities cascade via the User relationship.
    session.delete(u)
    session.commit()
