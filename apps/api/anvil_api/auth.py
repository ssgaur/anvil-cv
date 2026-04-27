"""JWT auth + password hashing + RBAC role gating.

Real JWT (HS256) + argon2 password hashing. OAuth identities live in
`UserOAuthIdentity` and are wired up via `routes/oauth.py`.

Admin bootstrap reads `ANVIL_ADMIN_EMAIL` / `ANVIL_ADMIN_PASSWORD` from the
environment. In production (`ANVIL_ENV=production`), the default values are
rejected at config-load time (see `config.py`).

RBAC:
  * Token contains only `sub` — role is loaded from DB on every request, so
    role changes take effect immediately (no JWT revocation needed).
  * `require_role(*roles)` returns a FastAPI dependency. Aliases below cover
    the common cases (`RequireAdmin`, `RequireReviewer`, `RequireLabeler`,
    `RequireAnyAuthed`).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlmodel import Session, select

from anvil_api.config import settings
from anvil_api.db import get_session
from anvil_api.models import User, UserRole

log = logging.getLogger("anvil.auth")

pwd = CryptContext(schemes=["argon2"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def normalize_email(email: str) -> str:
    """Lowercase + strip; canonical form for storage and comparison."""
    return email.strip().lower()


def hash_password(p: str) -> str:
    return pwd.hash(p)


def verify_password(p: str, h: str | None) -> bool:
    """Verify password; returns False if the user has no local password (OAuth-only)."""
    if not h:
        return False
    try:
        return pwd.verify(p, h)
    except (ValueError, TypeError):
        return False


def make_token(user_id: UUID) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.jwt_expires_minutes)
    return jwt.encode(
        {"sub": str(user_id), "iat": int(now.timestamp()), "exp": exp},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = UUID(payload["sub"])
    except (JWTError, ValueError, KeyError) as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from e

    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
SessionDep = Annotated[Session, Depends(get_session)]


# ---------- RBAC helpers ----------
_ROLE_RANK = {
    UserRole.VIEWER: 0,
    UserRole.LABELER: 1,
    UserRole.REVIEWER: 2,
    UserRole.ADMIN: 3,
}


def role_at_least(user: User, minimum: UserRole) -> bool:
    return _ROLE_RANK.get(user.role, -1) >= _ROLE_RANK[minimum]


def require_role(*allowed: UserRole):
    """FastAPI dependency factory — 403 if user.role not in allowed.

    Use the precomputed aliases below where possible.
    """
    allowed_set = set(allowed)

    def _dep(user: CurrentUser) -> User:
        if user.role not in allowed_set:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"role '{user.role}' is not permitted (need one of: "
                f"{', '.join(sorted(r.value for r in allowed_set))})",
            )
        return user

    return _dep


# Hierarchy aliases — admin is implicitly included in every "X+" alias.
RequireAdmin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
RequireReviewer = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.REVIEWER))]
RequireLabeler = Annotated[
    User, Depends(require_role(UserRole.ADMIN, UserRole.REVIEWER, UserRole.LABELER))
]
RequireAnyAuthed = Annotated[
    User,
    Depends(require_role(UserRole.ADMIN, UserRole.REVIEWER, UserRole.LABELER, UserRole.VIEWER)),
]


def bootstrap_admin(session: Session) -> User | None:
    """Ensure a bootstrap admin exists when configured.

    Behavior:
      * If `ANVIL_ADMIN_BOOTSTRAP=false`, only logs a warning when no admin
        exists in the DB.
      * Else: looks up `ANVIL_ADMIN_EMAIL`. If a user with that email exists,
        idempotently promotes them to `admin` (break-glass behavior; admins
        cannot permanently demote the bootstrap account while the env var is
        set). Otherwise creates a new admin user with `ANVIL_ADMIN_PASSWORD`.

    Always emits a WARNING if the database contains zero admins after this
    runs — that means project create/delete and user management are
    unavailable until an admin is provisioned.
    """
    email = normalize_email(settings.admin_email) if settings.admin_email else ""
    password = settings.admin_password

    user: User | None = None
    if settings.admin_bootstrap and email and password:
        existing = session.exec(select(User).where(User.email == email)).first()
        if existing:
            if existing.role != UserRole.ADMIN:
                existing.role = UserRole.ADMIN
                session.add(existing)
                session.commit()
                session.refresh(existing)
                log.info("auth: promoted existing user %s to admin (bootstrap)", email)
            user = existing
        else:
            user = User(
                email=email,
                name="Admin",
                password_hash=hash_password(password),
                role=UserRole.ADMIN,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            log.info("auth: created bootstrap admin %s", email)

    n_admins = session.exec(
        select(User).where(User.role == UserRole.ADMIN)
    ).first()
    if not n_admins:
        log.warning(
            "auth: no admin user exists. Set ANVIL_ADMIN_EMAIL/ANVIL_ADMIN_PASSWORD "
            "and restart, or promote a user via SQL: "
            "UPDATE users SET role='admin' WHERE email='you@example.com';"
        )
    return user
