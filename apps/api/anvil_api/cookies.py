"""Centralized HttpOnly cookie helpers for access + refresh tokens.

All login/signup/setup/oauth/refresh paths must use these helpers so cookie
attributes (Path, SameSite, Secure, Max-Age) stay consistent. Mismatched
attributes between set/clear are the most common source of "logout doesn't
log me out" bugs.

Cookie scoping:
  * `anvil_access`   — Path=/, sent on every API call so RBAC works on data
    routes. Lifetime matches the JWT exp.
  * `anvil_refresh`  — Path=/api/auth, only sent to auth endpoints
    (refresh, logout, logout-everywhere, password-reset). Limits exposure
    if a CSRF/XSS bug lets a non-auth route leak headers somewhere.
"""
from __future__ import annotations

from fastapi import Response

from anvil_api.config import settings

ACCESS_COOKIE = "anvil_access"
REFRESH_COOKIE = "anvil_refresh"
ACCESS_PATH = "/"
REFRESH_PATH = "/api/auth"


def _samesite() -> str:
    # "lax" | "strict" | "none". "none" requires Secure=True.
    val = (settings.cookie_samesite or "lax").lower()
    if val not in ("lax", "strict", "none"):
        return "lax"
    return val


def _secure() -> bool:
    # Production → always Secure; SameSite=None → forced Secure regardless.
    return bool(settings.is_production or _samesite() == "none")


def set_access_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        max_age=max_age_seconds,
        path=ACCESS_PATH,
        httponly=True,
        secure=_secure(),
        samesite=_samesite(),
        domain=settings.cookie_domain or None,
    )


def clear_access_cookie(response: Response) -> None:
    # delete_cookie must match path/domain or browser keeps the cookie.
    response.delete_cookie(
        ACCESS_COOKIE, path=ACCESS_PATH, domain=settings.cookie_domain or None,
    )


def set_refresh_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max_age_seconds,
        path=REFRESH_PATH,
        httponly=True,
        secure=_secure(),
        samesite=_samesite(),
        domain=settings.cookie_domain or None,
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_PATH, domain=settings.cookie_domain or None,
    )
