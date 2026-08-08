"""Structured JSON logging for the ML service.

Kept self-contained (rather than importing from anvil_api) because the ML
container image only copies `apps/ml` and `vendor/sam2`. The output shape
matches the API's JsonFormatter so a single aggregated stream stays uniform.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

_SKIP = {
    "args", "msg", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created",
    "msecs", "relativeCreated", "thread", "threadName", "processName",
    "process", "name", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "service": "ml",
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in _SKIP:
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = str(v)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(level)
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False


def memory_report() -> dict[str, Any]:
    """Best-effort container memory picture, used for OOM-risk warnings.

    Reads the cgroup v2 files Docker exposes. Returns an empty dict when they
    are unavailable (e.g. running natively on macOS).
    """
    out: dict[str, Any] = {}
    try:
        with open("/sys/fs/cgroup/memory.max") as f:
            raw = f.read().strip()
        limit = None if raw == "max" else int(raw)
        with open("/sys/fs/cgroup/memory.current") as f:
            current = int(f.read().strip())
        out["memory_used_mb"] = round(current / 1048576, 1)
        if limit:
            out["memory_limit_mb"] = round(limit / 1048576, 1)
            out["memory_used_pct"] = round(current / limit * 100, 1)
    except (OSError, ValueError):
        return {}
    return out
