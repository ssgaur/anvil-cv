"""CSRF defense for cookie-authenticated state-changing requests.

Strategy (per OWASP "custom request header" pattern, sufficient for SameSite=Lax
cookies + restricted CORS):

  * GET/HEAD/OPTIONS: never blocked.
  * Authorization-bearer requests: never blocked (CLI/scripts; not browser).
  * Browser requests with cookie auth on POST/PUT/PATCH/DELETE MUST send:
      X-Anvil-CSRF: 1
      AND a same-origin Origin / Referer header matching one of cors_origins.
  * Endpoints under /api/auth/login, /signup, /password-reset/* are exempted
    so unauthenticated submitters (no cookie yet) and the e2e/CLI clients
    keep working.

This is sufficient because:
  - SameSite=Lax already blocks cross-site POST cookie-attached requests in
    every modern browser.
  - To send a custom header from a different origin a browser must complete a
    CORS preflight; our `cors_origins` list rejects unknown origins, so the
    preflight fails before the unsafe request is sent.
  - Same-origin XHR can set custom headers freely — the legitimate frontend
    just adds X-Anvil-CSRF=1.

NOT sufficient if SameSite=None is required (cross-site SPA+API). In that
case ALSO add a synchronizer/double-submit token; that mode is intentionally
left out until/unless a deployment needs it.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from anvil_api.config import settings
from anvil_api.cookies import ACCESS_COOKIE, REFRESH_COOKIE

_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_HEADER = "x-anvil-csrf"
_CSRF_VALUE = "1"

# Endpoints that must remain reachable without a CSRF header so that
# unauthenticated users (no cookie yet) and CLI tools can hit them.
# Anything that issues a NEW session is fine — there's no existing cookie
# to forge.
_EXEMPT_PREFIXES = (
    "/api/auth/login",
    "/api/auth/signup",
    "/api/auth/password-reset",
    "/api/auth/oauth/",          # provider redirects + callbacks
    "/api/setup/initialize",
)


def _allowed_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()}


def _origin_ok(request: Request) -> bool:
    """True iff Origin (or, fallback, Referer) is in cors_origins.

    Requests issued via fetch from the browser always carry Origin on unsafe
    methods. Native curl/Python clients typically don't — we exempt those by
    falling back to the bearer-token path below.
    """
    allowed = _allowed_origins()
    if not allowed:
        return False
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/") in allowed
    referer = request.headers.get("referer")
    if referer:
        try:
            sp = urlsplit(referer)
            base = f"{sp.scheme}://{sp.netloc}"
            return base.rstrip("/") in allowed
        except Exception:
            return False
    return False


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Any],
    ) -> Response:
        method = request.method.upper()
        if method not in _UNSAFE:
            return await call_next(request)

        path = request.url.path
        for prefix in _EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # Bearer-token clients (CLI/e2e) bypass CSRF — they do NOT carry the
        # cookie and CSRF only matters when the browser auto-attaches one.
        auth_header = request.headers.get("authorization", "")
        has_bearer = auth_header.lower().startswith("bearer ")
        if has_bearer:
            return await call_next(request)

        # No cookie present? Then this is just an anonymous request that will
        # 401 downstream — nothing to forge.
        cookies = request.cookies
        if ACCESS_COOKIE not in cookies and REFRESH_COOKIE not in cookies:
            return await call_next(request)

        csrf_header = request.headers.get(_CSRF_HEADER, "")
        if csrf_header != _CSRF_VALUE or not _origin_ok(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "csrf check failed"},
            )
        return await call_next(request)
