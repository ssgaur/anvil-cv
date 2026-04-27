from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from anvil_api.auth import (
    CurrentUser,
    SessionDep,
    hash_password,
    make_token,
    normalize_email,
    verify_password,
)
from anvil_api.config import settings
from anvil_api.models import User, UserRole
from anvil_api.schemas import LoginIn, ProvidersOut, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _resolve_default_signup_role() -> UserRole:
    """Map config string → UserRole; falls back to VIEWER on bad input."""
    try:
        return UserRole(settings.default_signup_role.lower())
    except ValueError:
        return UserRole.VIEWER


@router.get("/providers", response_model=ProvidersOut)
def providers() -> ProvidersOut:
    """Public discovery: which auth methods are enabled on this deployment."""
    return ProvidersOut(
        password=True,
        signup=settings.allow_signup,
        oauth={
            "google": settings.google_oauth_enabled,
            "github": settings.github_oauth_enabled,
        },
    )


@router.post("/signup", response_model=TokenOut)
def signup(body: SignupIn, session: SessionDep) -> TokenOut:
    if not settings.allow_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "signup is disabled on this deployment")
    email = normalize_email(body.email)
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")
    user = User(
        email=email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=_resolve_default_signup_role(),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return TokenOut(access_token=make_token(user.id))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, session: SessionDep) -> TokenOut:
    email = normalize_email(body.email)
    user = session.exec(select(User).where(User.email == email)).first()
    # Same generic 401 whether user is missing, OAuth-only, or wrong password.
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return TokenOut(access_token=make_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, name=user.name,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        created_at=user.created_at,
    )
