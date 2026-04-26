"""JWT auth + password hashing.

Real JWT (HS256) + argon2 password hashing. OAuth identities live in
`UserOAuthIdentity` and are wired up via `routes/oauth.py`.

Admin bootstrap reads `ANVIL_ADMIN_EMAIL` / `ANVIL_ADMIN_PASSWORD` from the
environment. In production (`ANVIL_ENV=production`), the default values are
rejected at config-load time (see `config.py`).
"""
from __future__ import annotations

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
from anvil_api.models import User

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


def bootstrap_admin(session: Session) -> User | None:
    """Ensure a bootstrap admin exists when configured.

    Reads `ANVIL_ADMIN_EMAIL` + `ANVIL_ADMIN_PASSWORD` from settings. If either
    is empty, bootstrap is skipped (deployment manages users via signup/OAuth).
    If a user with that email already exists, the password is left alone.
    """
    email = normalize_email(settings.admin_email) if settings.admin_email else ""
    password = settings.admin_password
    if not settings.admin_bootstrap or not email or not password:
        return None
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return existing
    admin = User(email=email, name="Admin", password_hash=hash_password(password))
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin
