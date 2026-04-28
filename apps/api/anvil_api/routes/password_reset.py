"""Password reset request + confirm endpoints.

Design:

* `/password-reset/request` — always returns 204, regardless of whether the
  email maps to an account. This prevents enumeration. We *do* hit a per-IP
  limiter that DOES return 429 (an attacker burning addresses learns nothing
  account-specific from the throttle). A per-email limiter is also enforced
  so a single victim can't be spammed by an attacker chasing a leak.

* Token transport: a 48-byte URL-safe random token is delivered to the user
  via the configured email backend; only its HMAC-SHA256 lives in the DB
  (`password_reset_tokens.token_hash`). The link is
  `${FRONTEND_URL}/reset?token=<raw>`.

* `/password-reset/confirm` — atomic, single-use:
    1. SELECT FOR UPDATE the row by token_hash;
    2. reject if used / revoked / expired;
    3. update the user's password_hash, bump token_version (invalidates every
       outstanding access JWT for the user), revoke ALL refresh-token
       families (logout-everywhere semantics), mark the row used,
       invalidate sibling unused reset rows for the user;
    4. clear cookies on the response so the previous browser session is
       fully gone.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import update
from sqlmodel import select

from anvil_api.auth import (
    SessionDep,
    hash_password,
    hmac_reset_token,
    normalize_email,
)
from anvil_api.config import settings
from anvil_api.cookies import clear_access_cookie, clear_refresh_cookie
from anvil_api.email import password_reset_email, send_email
from anvil_api.models import PasswordResetToken, User, UserRefreshToken
from anvil_api.ratelimit import (
    hit_password_reset_email,
    hit_password_reset_ip,
)

log = logging.getLogger("anvil.auth.reset")

router = APIRouter(prefix="/auth/password-reset", tags=["auth"])


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    password: str = Field(min_length=4, max_length=255)


def _utcnow():
    from datetime import UTC, datetime
    return datetime.now(UTC).replace(tzinfo=None)


@router.post(
    "/request",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(hit_password_reset_ip)],
)
def request_reset(
    body: PasswordResetRequestIn, session: SessionDep, response: Response,
) -> Response:
    email = normalize_email(body.email)
    # Per-email throttle: silent — never leak whether the email exists.
    try:
        hit_password_reset_email(email)
    except HTTPException:
        # Rate limited: still return 204 so an attacker cannot distinguish
        # "this address is being reset a lot" from "this address doesn't exist".
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    user = session.exec(select(User).where(User.email == email)).first()
    if user is not None:
        raw = secrets.token_urlsafe(48)
        ttl = int(settings.password_reset_ttl_minutes)
        row = PasswordResetToken(
            user_id=user.id,
            token_hash=hmac_reset_token(raw),
            expires_at=_utcnow() + timedelta(minutes=ttl),
        )
        session.add(row)
        session.commit()
        link = f"{settings.frontend_url.rstrip('/')}/reset?token={quote(raw)}"
        oauth_only = not bool(user.password_hash)
        subj, body_text = password_reset_email(user.email, link, oauth_only=oauth_only)
        send_email(user.email, subj, body_text)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(hit_password_reset_ip)],
)
def confirm_reset(
    body: PasswordResetConfirmIn,
    session: SessionDep,
    response: Response,
) -> Response:
    h = hmac_reset_token(body.token)
    row = session.exec(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == h)
        .with_for_update(),
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired token")
    now = _utcnow()
    if row.used_at is not None or row.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired token")

    user = session.get(User, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired token")

    # Atomic state transition.
    user.password_hash = hash_password(body.password)
    user.token_version = int(user.token_version) + 1
    session.add(user)

    row.used_at = now
    session.add(row)

    # Invalidate any sibling reset tokens for this user.
    session.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != row.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now),
    )

    # Revoke all refresh-token families: forces sign-in everywhere.
    session.execute(
        update(UserRefreshToken)
        .where(
            UserRefreshToken.user_id == user.id,
            UserRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_reason="password-reset"),
    )

    session.commit()

    clear_access_cookie(response)
    clear_refresh_cookie(response)
    log.info("password reset for user_id=%s", user.id)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
