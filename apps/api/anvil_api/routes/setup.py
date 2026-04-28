"""First-run setup wizard.

Endpoints (no auth required):
  GET  /api/setup/status      -> {"needs_setup": bool}
  POST /api/setup/initialize  -> create the very first admin and lock setup

Concurrency: serialized by a Postgres advisory lock (xact-scoped) so two
concurrent installers can't both create competing first-admins. Once setup
completes, a row is written into `instance_state` (`setup_completed_at`); the
endpoint then permanently rejects further calls — even if all admins are
later deleted (recovery requires CLI/env reset, not a public web endpoint).

Signup-while-no-admin is also blocked elsewhere (routes/auth.py) so a public
deployment can't accumulate orphan viewer accounts before setup.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlmodel import select

from anvil_api.auth import (
    SessionDep,
    access_token_max_age_seconds,
    hash_password,
    make_token,
    normalize_email,
)
from anvil_api.cookies import set_access_cookie
from anvil_api.models import InstanceState, User, UserRole
from anvil_api.ratelimit import hit_signup_ip
from anvil_api.schemas import TokenOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

# Stable arbitrary key for pg_advisory_xact_lock. Single integer namespace.
_SETUP_LOCK_KEY = 0xA0001
_SETUP_DONE_KEY = "setup_completed_at"


class SetupStatusOut(BaseModel):
    needs_setup: bool


class SetupInitIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


def _setup_completed(session) -> bool:
    row = session.get(InstanceState, _SETUP_DONE_KEY)
    return row is not None and row.value is not None


def _admin_exists(session) -> bool:
    row = session.exec(
        select(User.id).where(User.role == UserRole.ADMIN.value).limit(1)
    ).first()
    return row is not None


def needs_setup(session) -> bool:
    """True iff first-run wizard should be shown.

    Strict definition: no admin AND setup-completed marker is unset. Once the
    marker is set, setup stays closed permanently (admin recovery is an
    out-of-band CLI/env operation, not a public endpoint).
    """
    if _setup_completed(session):
        return False
    return not _admin_exists(session)


@router.get("/status", response_model=SetupStatusOut)
def status_(session: SessionDep) -> SetupStatusOut:
    return SetupStatusOut(needs_setup=needs_setup(session))


@router.post(
    "/initialize",
    response_model=TokenOut,
    dependencies=[Depends(hit_signup_ip)],
)
def initialize(body: SetupInitIn, session: SessionDep, request: Request, response: Response) -> TokenOut:
    # Serialize concurrent installers (Postgres only — SQLite would silently
    # no-op the lock; we are Postgres-only per project decision).
    try:
        session.exec(text(f"SELECT pg_advisory_xact_lock({_SETUP_LOCK_KEY})"))
    except Exception as e:
        log.warning("advisory lock failed (non-postgres backend?): %s", e)

    # Re-check inside the lock.
    if _setup_completed(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "setup already completed")
    if _admin_exists(session):
        # Mark setup completed so subsequent calls fast-path to 409.
        session.merge(InstanceState(
            key=_SETUP_DONE_KEY, value=datetime.utcnow().isoformat(),
        ))
        session.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "setup already completed")

    email = normalize_email(body.email)
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing is not None:
        # Edge: viewer/labeler/etc was created before admin (shouldn't happen
        # because signup is blocked when needs_setup, but be defensive).
        # Promote them to admin and set their password.
        existing.role = UserRole.ADMIN  # type: ignore[assignment]
        existing.name = body.name
        existing.password_hash = hash_password(body.password)
        session.add(existing)
        new_admin = existing
    else:
        new_admin = User(
            email=email,
            name=body.name,
            password_hash=hash_password(body.password),
            role=UserRole.ADMIN,
        )
        session.add(new_admin)

    session.merge(InstanceState(
        key=_SETUP_DONE_KEY, value=datetime.utcnow().isoformat(),
    ))
    session.commit()
    session.refresh(new_admin)
    log.info("setup wizard: created admin %s", new_admin.email)
    token = make_token(new_admin.id, token_version=new_admin.token_version)
    set_access_cookie(response, token, access_token_max_age_seconds())
    from anvil_api.routes.refresh import issue_refresh
    issue_refresh(session, response, new_admin, request)
    session.commit()
    return TokenOut(access_token=token)
