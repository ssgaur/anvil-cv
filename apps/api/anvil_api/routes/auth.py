from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import select

from anvil_api.auth import (
    CurrentUser,
    SessionDep,
    access_token_max_age_seconds,
    hash_password,
    make_token,
    normalize_email,
    verify_password,
)
from anvil_api.config import settings
from anvil_api.cookies import (
    clear_access_cookie,
    clear_refresh_cookie,
    set_access_cookie,
)
from anvil_api.models import User, UserRole
from anvil_api.ratelimit import (
    hit_login_email,
    hit_login_ip,
    hit_signup_ip,
    reset_login_email,
)
from anvil_api.schemas import LoginIn, ProvidersOut, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _resolve_default_signup_role() -> UserRole:
    """Map config string → UserRole; falls back to VIEWER on bad input."""
    try:
        return UserRole(settings.default_signup_role.lower())
    except ValueError:
        return UserRole.VIEWER


def _issue_session(response: Response, user: User) -> str:
    """Mint an access token, set the access cookie, return the token string.

    Refresh-token issuance is wired separately in routes/refresh.py so that
    bearer-only callers (CLI/e2e) can opt out of refresh handling cleanly.
    """
    token = make_token(user.id, token_version=user.token_version)
    set_access_cookie(response, token, access_token_max_age_seconds())
    return token


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


@router.post("/signup", response_model=TokenOut, dependencies=[Depends(hit_signup_ip)])
def signup(body: SignupIn, session: SessionDep, response: Response) -> TokenOut:
    if not settings.allow_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "signup is disabled on this deployment")
    # Block signups while no admin exists — otherwise we'd accumulate orphan
    # viewer accounts that no one can promote. Operator must run setup first.
    from anvil_api.routes.setup import needs_setup as _needs_setup
    if _needs_setup(session):
        raise HTTPException(
            status.HTTP_423_LOCKED,
            "instance not set up yet — visit /setup to create the first admin",
        )
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
    return TokenOut(access_token=_issue_session(response, user))


@router.post("/login", response_model=TokenOut, dependencies=[Depends(hit_login_ip)])
def login(body: LoginIn, session: SessionDep, request: Request, response: Response) -> TokenOut:
    email = normalize_email(body.email)
    # Per-email throttle: counts only failed attempts (reset on success below)
    # so an attacker spamming wrong passwords cannot lock out the real user
    # because *they* trip 429 first; a real user with the correct password
    # still succeeds and clears the counter.
    user = session.exec(select(User).where(User.email == email)).first()
    if not user or not verify_password(body.password, user.password_hash):
        # Record this failure against the email; if it pushes them over the
        # per-email cap, this raises 429 instead of 401 — by design.
        try:
            hit_login_email(email)
        except HTTPException:
            raise
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    # Successful login → clear the per-email failure counter.
    reset_login_email(email)
    return TokenOut(access_token=_issue_session(response, user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    """Clear browser cookies. Refresh-token row revocation (when present) is
    handled in routes/refresh.py — calling that path is preferred for browsers,
    but this endpoint stays available as a no-side-effect cookie wipe."""
    clear_access_cookie(response)
    clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, name=user.name,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        created_at=user.created_at,
    )
