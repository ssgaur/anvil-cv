"""OAuth login (Google + GitHub).

Flow per provider:

  1. GET  /api/auth/oauth/{provider}/login
       - generates a random nonce
       - sets HttpOnly SameSite=Lax cookie `anvil_oauth_nonce`
       - signs a short-lived JWT state token containing {nonce, provider, exp}
       - 302-redirects to the provider authorization URL with that state
  2. Provider redirects back to:
     GET /api/auth/oauth/{provider}/callback?code=...&state=...
       - verifies state JWT signature/exp/provider
       - verifies state.nonce == cookie nonce  (double-submit; CSRF/login-CSRF defense)
       - exchanges code for an access token
       - fetches userinfo and a *verified* email
            * Google: requires `email_verified == true`
            * GitHub: requires a `primary && verified` email from /user/emails
       - upserts User + UserOAuthIdentity (linking by verified email allowed)
       - mints an Anvil JWT and 302-redirects to
         `${FRONTEND_URL}/auth/callback#token=<jwt>`
       - clears the nonce cookie
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlmodel import select

from anvil_api.auth import SessionDep, make_token, normalize_email
from anvil_api.config import settings
from anvil_api.models import User, UserOAuthIdentity, UserRole

router = APIRouter(prefix="/auth/oauth", tags=["auth"])

NONCE_COOKIE = "anvil_oauth_nonce"
STATE_TTL_SECONDS = 600  # 10 minutes


# --- Provider config (server-side only) ---
_GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public OAuth endpoint
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_SCOPES = "openid email profile"

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
_GITHUB_SCOPES = "read:user user:email"


def _provider_enabled(provider: str) -> tuple[str, str]:
    """Return (client_id, client_secret) or raise 404 if not enabled."""
    if provider == "google" and settings.google_oauth_enabled:
        return settings.google_client_id, settings.google_client_secret
    if provider == "github" and settings.github_oauth_enabled:
        return settings.github_client_id, settings.github_client_secret
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"oauth provider not configured: {provider}")


def _redirect_uri(provider: str) -> str:
    base = settings.oauth_redirect_base.rstrip("/")
    return f"{base}/api/auth/oauth/{provider}/callback"


def _sign_state(nonce: str, provider: str) -> str:
    exp = datetime.now(UTC) + timedelta(seconds=STATE_TTL_SECONDS)
    return jwt.encode(
        {"nonce": nonce, "provider": provider, "exp": exp},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _verify_state(state: str, provider: str, cookie_nonce: str | None) -> None:
    if not cookie_nonce:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing oauth nonce cookie")
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid oauth state") from e
    if payload.get("provider") != provider:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "oauth state provider mismatch")
    if not secrets.compare_digest(str(payload.get("nonce", "")), cookie_nonce):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "oauth state nonce mismatch")


@router.get("/{provider}/login")
def oauth_login(provider: str, response: Response) -> RedirectResponse:
    if provider not in ("google", "github"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider")
    client_id, _ = _provider_enabled(provider)

    nonce = secrets.token_urlsafe(32)
    state = _sign_state(nonce, provider)

    if provider == "google":
        params = {
            "client_id": client_id,
            "redirect_uri": _redirect_uri(provider),
            "response_type": "code",
            "scope": _GOOGLE_SCOPES,
            "state": state,
            "access_type": "online",
            "prompt": "select_account",
        }
        url = _GOOGLE_AUTHORIZE_URL + "?" + httpx.QueryParams(params).__str__()
    else:  # github
        params = {
            "client_id": client_id,
            "redirect_uri": _redirect_uri(provider),
            "scope": _GITHUB_SCOPES,
            "state": state,
            "allow_signup": "true",
        }
        url = _GITHUB_AUTHORIZE_URL + "?" + httpx.QueryParams(params).__str__()

    redirect = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    redirect.set_cookie(
        key=NONCE_COOKIE,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )
    return redirect


def _failure_redirect(reason: str) -> RedirectResponse:
    base = settings.frontend_url.rstrip("/")
    return RedirectResponse(f"{base}/login?oauth_error={reason}", status_code=302)


def _success_redirect(token: str) -> RedirectResponse:
    base = settings.frontend_url.rstrip("/")
    # Token is passed in the URL fragment so it never reaches any server.
    return RedirectResponse(f"{base}/auth/callback#token={token}", status_code=302)


async def _google_userinfo(code: str) -> tuple[str, str, str]:
    """Exchange code -> (subject, verified_email, name). Raises HTTPException on failure."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": _redirect_uri("google"),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "google token exchange failed")
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "google token missing")

        info_resp = await client.get(
            _GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if info_resp.status_code != 200:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "google userinfo failed")
        info: dict[str, Any] = info_resp.json()

    if not info.get("email_verified"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "google email not verified")
    subject = str(info.get("sub", ""))
    email = str(info.get("email", ""))
    name = str(info.get("name") or info.get("email") or "User")
    if not subject or not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "google userinfo incomplete")
    return subject, email, name


async def _github_userinfo(code: str) -> tuple[str, str, str]:
    """Exchange code -> (subject, verified_email, name). Raises HTTPException on failure."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            _GITHUB_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "redirect_uri": _redirect_uri("github"),
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "github token exchange failed")
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "github token missing")

        auth_h = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        }
        user_resp = await client.get(_GITHUB_USER_URL, headers=auth_h)
        emails_resp = await client.get(_GITHUB_EMAILS_URL, headers=auth_h)
        if user_resp.status_code != 200 or emails_resp.status_code != 200:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "github user/emails fetch failed")
        user: dict[str, Any] = user_resp.json()
        emails: list[dict[str, Any]] = emails_resp.json()

    # Require a primary AND verified email (GitHub's /user.email is unreliable).
    verified_email: str | None = None
    for e in emails:
        if e.get("primary") and e.get("verified") and e.get("email"):
            verified_email = str(e["email"])
            break
    if not verified_email:
        # Fall back to any verified email
        for e in emails:
            if e.get("verified") and e.get("email"):
                verified_email = str(e["email"])
                break
    if not verified_email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "github has no verified email")

    subject = str(user.get("id", ""))
    name = str(user.get("name") or user.get("login") or verified_email)
    if not subject:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "github user missing id")
    return subject, verified_email, name


def _upsert_oauth_user(
    session: SessionDep,
    *,
    provider: str,
    subject: str,
    email: str,
    name: str,
) -> UUID:
    """Find-or-create the user + identity. Returns the user id.

    Linking rule: if an existing identity (provider, subject) is present we use
    it. Otherwise we look up a user by verified email; if found we link by
    creating a new identity. Otherwise we create a brand-new user with no
    password and the linked identity.
    """
    email_n = normalize_email(email)

    identity = session.exec(
        select(UserOAuthIdentity).where(
            UserOAuthIdentity.provider == provider,
            UserOAuthIdentity.subject == subject,
        )
    ).first()
    if identity:
        return identity.user_id

    user = session.exec(select(User).where(User.email == email_n)).first()
    if user is None:
        try:
            new_role = UserRole(settings.default_signup_role.lower())
        except ValueError:
            new_role = UserRole.VIEWER
        user = User(email=email_n, name=name, password_hash=None, role=new_role)
        session.add(user)
        session.commit()
        session.refresh(user)

    identity = UserOAuthIdentity(
        user_id=user.id, provider=provider, subject=subject, email=email_n,
    )
    session.add(identity)
    session.commit()
    return user.id


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    session: SessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    if provider not in ("google", "github"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider")
    _provider_enabled(provider)  # 404 if creds missing

    if error:
        return _failure_redirect(error)
    if not code or not state:
        return _failure_redirect("missing_code_or_state")

    cookie_nonce = request.cookies.get(NONCE_COOKIE)
    try:
        _verify_state(state, provider, cookie_nonce)
    except HTTPException as e:
        resp = _failure_redirect(f"state_{e.detail}".replace(" ", "_"))
        resp.delete_cookie(NONCE_COOKIE, path="/")
        return resp

    try:
        if provider == "google":
            subject, email, name = await _google_userinfo(code)
        else:
            subject, email, name = await _github_userinfo(code)
    except HTTPException as e:
        resp = _failure_redirect(str(e.detail).replace(" ", "_"))
        resp.delete_cookie(NONCE_COOKIE, path="/")
        return resp

    user_id = _upsert_oauth_user(
        session, provider=provider, subject=subject, email=email, name=name,
    )
    token = make_token(user_id)
    resp = _success_redirect(token)
    resp.delete_cookie(NONCE_COOKIE, path="/")
    return resp
