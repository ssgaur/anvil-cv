"""Rotating refresh-token endpoints for cookie sessions.

Refresh tokens live in `user_refresh_tokens`. Each row stores the
HMAC-SHA256(JWT_SECRET, raw) of the raw token (delivered to the browser as
`anvil_refresh`, HttpOnly, Path=/api/auth). Each refresh:

  * SELECT ... FOR UPDATE the row by its hash (Postgres advisory lock on the
    row prevents the multi-tab race),
  * if it's still unused & unrevoked & unexpired → mark used, mint a new
    raw token, persist a child row sharing the same `family_id`, set fresh
    cookies and return a new access token,
  * if it's already used: within the configured grace window it's treated
    as a benign concurrent refresh and returns 409 (frontend retries with
    the latest cookie); past the window it's treated as token theft and the
    *entire family* is revoked, including any previously-issued descendants.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import update
from sqlmodel import select

from anvil_api.auth import (
    SessionDep,
    access_token_max_age_seconds,
    hmac_refresh_token,
    make_token,
    refresh_token_max_age_seconds,
)
from anvil_api.config import settings
from anvil_api.cookies import (
    REFRESH_COOKIE,
    clear_access_cookie,
    clear_refresh_cookie,
    set_access_cookie,
    set_refresh_cookie,
)
from anvil_api.models import User, UserRefreshToken
from anvil_api.schemas import TokenOut

log = logging.getLogger("anvil.auth.refresh")

router = APIRouter(prefix="/auth", tags=["auth"])


def _utcnow():
    from datetime import UTC, datetime
    return datetime.now(UTC).replace(tzinfo=None)


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host[:64] if request.client else None


def issue_refresh(
    session: SessionDep,
    response: Response,
    user: User,
    request: Request,
    *,
    family_id: UUID | None = None,
    parent_id: UUID | None = None,
) -> str:
    """Mint a new refresh token, store it, attach the cookie. Returns the raw
    token (rarely needed by callers)."""
    raw = secrets.token_urlsafe(48)
    row = UserRefreshToken(
        user_id=user.id,
        family_id=family_id or uuid4(),
        parent_id=parent_id,
        token_hash=hmac_refresh_token(raw),
        user_agent=(request.headers.get("user-agent") or "")[:255] or None,
        ip=_client_ip(request),
        expires_at=_utcnow() + timedelta(days=int(settings.refresh_expires_days)),
    )
    session.add(row)
    session.flush()
    set_refresh_cookie(response, raw, refresh_token_max_age_seconds())
    return raw


def revoke_family(session: SessionDep, family_id: UUID, reason: str) -> int:
    """Mark every row in the chain revoked. Returns count revoked."""
    res = session.execute(
        update(UserRefreshToken)
        .where(
            UserRefreshToken.family_id == family_id,
            UserRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=_utcnow(), revoked_reason=reason),
    )
    return res.rowcount or 0


def revoke_all_for_user(session: SessionDep, user_id: UUID, reason: str) -> int:
    res = session.execute(
        update(UserRefreshToken)
        .where(
            UserRefreshToken.user_id == user_id,
            UserRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=_utcnow(), revoked_reason=reason),
    )
    return res.rowcount or 0


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, session: SessionDep) -> TokenOut:
    raw = request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing refresh token")
    h = hmac_refresh_token(raw)

    # SELECT ... FOR UPDATE serialises concurrent refreshes on the same row,
    # which is what makes the reuse check race-free.
    row = session.exec(
        select(UserRefreshToken)
        .where(UserRefreshToken.token_hash == h)
        .with_for_update(),
    ).first()
    if row is None:
        # Unknown token: scrub cookies and force re-login.
        clear_refresh_cookie(response)
        clear_access_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid refresh token")

    now = _utcnow()
    if row.revoked_at is not None or row.expires_at <= now:
        clear_refresh_cookie(response)
        clear_access_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token revoked or expired")

    if row.used_at is not None:
        grace = int(settings.refresh_reuse_grace_seconds)
        if (now - row.used_at).total_seconds() <= grace:
            # Benign multi-tab race: another tab already rotated. The browser
            # may not yet have the new cookie. Tell the client to retry.
            session.commit()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "concurrent refresh in progress, retry",
            )
        # Real reuse → token theft suspected. Burn the whole family.
        revoke_family(session, row.family_id, "reuse-detected")
        session.commit()
        clear_refresh_cookie(response)
        clear_access_cookie(response)
        log.warning("refresh-reuse detected for user_id=%s family=%s", row.user_id, row.family_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token reuse detected")

    user = session.get(User, row.user_id)
    if user is None:
        clear_refresh_cookie(response)
        clear_access_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")

    # Mark the consumed row used and mint child + new access token.
    row.used_at = now
    session.add(row)
    issue_refresh(
        session, response, user, request,
        family_id=row.family_id, parent_id=row.id,
    )
    new_access = make_token(user.id, token_version=user.token_version)
    set_access_cookie(response, new_access, access_token_max_age_seconds())
    session.commit()
    return TokenOut(access_token=new_access)


@router.post("/logout-everywhere", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(
    request: Request, response: Response, session: SessionDep,
) -> Response:
    """Revoke every refresh token for the calling user *and* bump their
    token_version, which immediately invalidates every outstanding access
    JWT (next request → 401 \"token revoked\")."""
    from anvil_api.auth import get_current_user
    # Reuse the standard dependency manually (this route is not declared
    # behind it because we want to clear cookies even on a partially-bad
    # session).
    try:
        user = get_current_user(  # type: ignore[arg-type]
            request, header_token=None, session=session,
        )
    except HTTPException:
        clear_access_cookie(response)
        clear_refresh_cookie(response)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response
    revoke_all_for_user(session, user.id, "logout-everywhere")
    user.token_version = int(user.token_version) + 1
    session.add(user)
    session.commit()
    clear_access_cookie(response)
    clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
