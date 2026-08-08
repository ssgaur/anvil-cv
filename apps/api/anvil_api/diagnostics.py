"""Incident logging with machine-actionable diagnosis.

Raw exceptions are useless on their own. `httpx.RemoteProtocolError: Server
disconnected without sending a response.` does not tell anyone that the ML
container was OOM-killed and that the fix is to raise the Docker VM memory.

Every incident emitted here carries a stable `code`, a `probable_cause`, and a
`remediation` string so a human *or* an LLM agent can act without reproducing
the failure. Incidents are also buffered in memory so `/api/diagnostics` and
`scripts/diagnose.py` can report recent failures without shell access to logs.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

log = logging.getLogger("anvil.incident")

# Keep the tail small: this is a debugging aid, not a log store.
_MAX_INCIDENTS = 200
_incidents: deque[dict[str, Any]] = deque(maxlen=_MAX_INCIDENTS)
_lock = threading.Lock()


class Incident:
    """A diagnosed failure mode: stable code + why it happened + how to fix it."""

    __slots__ = ("code", "probable_cause", "remediation", "severity")

    def __init__(
        self, code: str, probable_cause: str, remediation: str, severity: str = "error",
    ) -> None:
        self.code = code
        self.probable_cause = probable_cause
        self.remediation = remediation
        self.severity = severity


ML_SERVICE_DIED = Incident(
    code="ml_service_died",
    probable_cause=(
        "The ML service accepted the request then died mid-flight without "
        "responding. On a memory-capped Docker VM this is almost always the "
        "SAM2 image encoder being OOM-killed (container exit code 137)."
    ),
    remediation=(
        "Check `docker inspect anvil-cv-ml-1 --format '{{.State.OOMKilled}}'`. "
        "If true, raise the Docker/Rancher VM memory to at least 8GB (10GB "
        "recommended) and restart with `docker compose up -d`. Then re-run the "
        "failed work. SAM2 base_plus needs ~3-5GB on CPU by itself."
    ),
)

ML_SERVICE_UNREACHABLE = Incident(
    code="ml_service_unreachable",
    probable_cause=(
        "The ML service is not accepting connections at all, so it is stopped, "
        "crashed, still loading the SAM2 checkpoint, or bound to another host."
    ),
    remediation=(
        "Run `docker compose ps ml` and `docker compose logs --tail 50 ml`. "
        "Start it with `docker compose up -d ml` and confirm "
        "`curl -fsS http://localhost:8001/health`. Verify ML_SERVICE_URL matches "
        "where the service actually listens."
    ),
)

ML_SERVICE_TIMEOUT = Incident(
    code="ml_service_timeout",
    probable_cause=(
        "The ML service did not answer within the client timeout. Usually CPU "
        "inference on an under-provisioned VM, or heavy swapping under memory "
        "pressure."
    ),
    remediation=(
        "Increase Docker VM CPUs (6+ recommended) and memory, or switch to a "
        "smaller SAM2 variant. Check `docker stats` while the request runs."
    ),
)

ML_SERVICE_ERROR = Incident(
    code="ml_service_error",
    probable_cause="The ML service responded with an HTTP error status.",
    remediation=(
        "Read the response body in this incident, then "
        "`docker compose logs --tail 100 ml` for the matching traceback."
    ),
)

STORAGE_ERROR = Incident(
    code="storage_error",
    probable_cause=(
        "Reading or writing an image/embedding on the storage backend failed. "
        "Common causes: the API and ML containers not sharing the same volume, "
        "a missing STORAGE_ROOT mount, or a full disk."
    ),
    remediation=(
        "Confirm api and ml both mount the same `local_data:/data` volume in "
        "docker-compose.yml and that STORAGE_ROOT=/data in both. Check free "
        "space with `docker system df` and `df -h`."
    ),
)

UNKNOWN_ERROR = Incident(
    code="unknown_error",
    probable_cause="Unclassified failure. See the exception type and message.",
    remediation=(
        "Inspect the traceback in the service logs. If this recurs, add a "
        "classifier for it in apps/api/anvil_api/diagnostics.py."
    ),
)


def classify(exc: BaseException) -> Incident:
    """Map an exception onto a diagnosed failure mode."""
    # Imported lazily so this module stays importable without httpx.
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return UNKNOWN_ERROR

    if isinstance(exc, httpx.RemoteProtocolError):
        return ML_SERVICE_DIED
    if isinstance(exc, httpx.ConnectError):
        return ML_SERVICE_UNREACHABLE
    if isinstance(exc, httpx.TimeoutException):
        return ML_SERVICE_TIMEOUT
    if isinstance(exc, httpx.HTTPStatusError):
        return ML_SERVICE_ERROR
    if isinstance(exc, OSError):
        return STORAGE_ERROR
    return UNKNOWN_ERROR


def record(
    component: str,
    operation: str,
    exc: BaseException | None = None,
    incident: Incident | None = None,
    **context: Any,
) -> dict[str, Any]:
    """Log a diagnosed incident as JSON and buffer it for the diagnostics API.

    Returns the incident payload so callers can persist a summary alongside
    whatever record they already update (e.g. `Image.error`).
    """
    diag = incident or (classify(exc) if exc is not None else UNKNOWN_ERROR)
    detail = f"{type(exc).__name__}: {exc}" if exc is not None else ""

    payload: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "code": diag.code,
        "severity": diag.severity,
        "component": component,
        "operation": operation,
        "detail": detail[:500],
        "probable_cause": diag.probable_cause,
        "remediation": diag.remediation,
        "context": {k: str(v) for k, v in context.items() if v is not None},
    }

    with _lock:
        _incidents.append(payload)

    log.error(
        f"incident:{diag.code}",
        extra={
            "code": diag.code,
            "component": component,
            "operation": operation,
            "detail": payload["detail"],
            "probable_cause": diag.probable_cause,
            "remediation": diag.remediation,
            **{f"ctx_{k}": v for k, v in payload["context"].items()},
        },
    )
    return payload


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """Most recent incidents, newest first."""
    with _lock:
        items = list(_incidents)
    return items[-limit:][::-1]


def clear() -> None:
    with _lock:
        _incidents.clear()


def summarize(payload: dict[str, Any]) -> str:
    """One-line, self-explanatory summary safe to store on a DB record."""
    return f"[{payload['code']}] {payload['detail']} | fix: {payload['remediation']}"
