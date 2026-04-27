"""In-process metrics + JSON structured logging.

Avoids the Prometheus client dependency by hand-rolling a tiny exporter that
emits the Prometheus text exposition format. Counters/histograms are kept in
process — fine for single-replica deployments; for multi-replica setups the
operator should put a real Prometheus client behind a /metrics scrape proxy.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# Prometheus default histogram buckets (seconds) — covers 1ms to 10s.
_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


class _Counter:
    __slots__ = ("name", "help", "_values", "_lock")

    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.help = help_text
        self._values: dict[tuple[tuple[str, str], ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, labels: dict[str, str], by: float = 1.0) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + by

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self._lock:
            items = list(self._values.items())
        for key, val in items:
            out.append(f"{self.name}{_fmt_labels(key)} {val}")
        return out


class _Histogram:
    __slots__ = ("name", "help", "buckets", "_buckets_count", "_sum", "_count", "_lock")

    def __init__(
        self, name: str, help_text: str, buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
    ) -> None:
        self.name = name
        self.help = help_text
        self.buckets = buckets
        self._buckets_count: dict[tuple[tuple[str, str], ...], list[int]] = {}
        self._sum: dict[tuple[tuple[str, str], ...], float] = {}
        self._count: dict[tuple[tuple[str, str], ...], int] = {}
        self._lock = threading.Lock()

    def observe(self, labels: dict[str, str], value: float) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            counts = self._buckets_count.setdefault(key, [0] * len(self.buckets))
            # Increment only the smallest bucket the value fits into; render()
            # converts these per-bucket counts into the cumulative form Prom expects.
            for i, ub in enumerate(self.buckets):
                if value <= ub:
                    counts[i] += 1
                    break
            self._sum[key] = self._sum.get(key, 0.0) + value
            self._count[key] = self._count.get(key, 0) + 1

    def render(self) -> list[str]:
        out = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]
        with self._lock:
            keys = list(self._buckets_count.keys())
        for key in keys:
            counts = self._buckets_count[key]
            cumulative = 0
            for i, ub in enumerate(self.buckets):
                cumulative += counts[i]
                lbl = dict(key); lbl["le"] = _fmt_float(ub)
                out.append(
                    f"{self.name}_bucket{_fmt_labels(tuple(sorted(lbl.items())))} {cumulative}"
                )
            lbl = dict(key); lbl["le"] = "+Inf"
            out.append(
                f"{self.name}_bucket{_fmt_labels(tuple(sorted(lbl.items())))} {self._count[key]}"
            )
            out.append(f"{self.name}_sum{_fmt_labels(key)} {self._sum[key]}")
            out.append(f"{self.name}_count{_fmt_labels(key)} {self._count[key]}")
        return out


def _fmt_float(v: float) -> str:
    return f"{v:g}"


def _fmt_labels(items: tuple[tuple[str, str], ...]) -> str:
    if not items:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in items)
    return "{" + inner + "}"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


# ---------- Singletons ----------
http_requests_total = _Counter(
    "anvil_http_requests_total", "Total HTTP requests by route+method+status",
)
http_request_duration_seconds = _Histogram(
    "anvil_http_request_duration_seconds", "HTTP request latency in seconds",
)


# ---------- JSON logging ----------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Attach extras (skip noisy internals).
        for k, v in record.__dict__.items():
            if k in ("args", "msg", "levelname", "levelno", "pathname", "filename",
                     "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                     "created", "msecs", "relativeCreated", "thread", "threadName",
                     "processName", "process", "name", "taskName"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = str(v)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Replace root handlers with one stdout JSON handler. Idempotent."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(level)
    # Quiet uvicorn's access log (we emit our own per-request line).
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False


_log = logging.getLogger("anvil.http")


# ---------- Middleware ----------
class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Any],
    ) -> Response:
        start = time.monotonic()
        method = request.method
        # Use route template if matched; falls back to raw path otherwise.
        path = request.url.path
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
        except Exception:
            duration = time.monotonic() - start
            route = _route_template(request, path)
            labels = {"method": method, "route": route, "status": "500"}
            http_requests_total.inc(labels)
            http_request_duration_seconds.observe(
                {"method": method, "route": route}, duration,
            )
            _log.exception(
                "request_failed", extra={"method": method, "route": route, "duration_ms": round(duration * 1000, 2)},
            )
            raise
        duration = time.monotonic() - start
        route = _route_template(request, path)
        labels = {"method": method, "route": route, "status": str(status_code)}
        http_requests_total.inc(labels)
        http_request_duration_seconds.observe(
            {"method": method, "route": route}, duration,
        )
        # Sample logging: skip the metrics scrape and health pings to keep noise down.
        if route not in ("/metrics", "/health", "/ready"):
            _log.info(
                "request",
                extra={
                    "method": method, "route": route, "status": status_code,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
        return response


def _route_template(request: Request, fallback: str) -> str:
    try:
        route = request.scope.get("route")
        if route is not None and getattr(route, "path", None):
            return str(route.path)
    except Exception:
        pass
    return fallback


def render_metrics() -> str:
    lines: list[str] = []
    lines.extend(http_requests_total.render())
    lines.extend(http_request_duration_seconds.render())
    return "\n".join(lines) + "\n"


def install(app: ASGIApp) -> None:
    """Wire the metrics middleware onto an ASGI app."""
    if hasattr(app, "add_middleware"):
        app.add_middleware(MetricsMiddleware)
