#!/usr/bin/env python3
"""Aggregate every Anvil container's logs into one tailable JSONL stream.

Each Docker service logs to its own stdout, so diagnosing an incident means
running `docker compose logs` once per service and mentally merging them by
timestamp. This collector produces a single cumulative file
(`logs/anvil.jsonl`) that a human or an LLM agent can watch in one place.

Lines already emitted as JSON (api, ml) are merged so their fields stay
queryable; anything else is wrapped with the originating service name.

Usage:
    python3 scripts/logcollect.py              # follow, writes logs/anvil.jsonl
    python3 scripts/logcollect.py --since 30m  # include recent backlog
    python3 scripts/logcollect.py --once       # snapshot then exit

Query examples:
    grep '"level":"ERROR"' logs/anvil.jsonl | tail -20
    jq -c 'select(.code) | {ts,service,code,remediation}' logs/anvil.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "logs" / "anvil.jsonl"

# `docker compose logs` prefixes each line with "<service>-<n>  | ".
_PREFIX = re.compile(r"^(?P<service>[a-zA-Z0-9_.-]+?)-\d+\s*\|\s?(?P<rest>.*)$")
# Docker's --timestamps emits RFC3339Nano before the payload.
_TS = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T[\d:.]+Z?)\s+(?P<rest>.*)$")

_MAX_BYTES = 32 * 1024 * 1024


def parse_line(raw: str) -> dict[str, Any] | None:
    line = raw.rstrip("\n")
    if not line.strip():
        return None

    service = "unknown"
    m = _PREFIX.match(line)
    if m:
        service = m.group("service")
        line = m.group("rest")

    docker_ts = None
    m = _TS.match(line)
    if m:
        docker_ts = m.group("ts")
        line = m.group("rest")

    line = line.strip()
    if not line:
        return None

    # Merge structured logs so their fields stay queryable.
    if line.startswith("{"):
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                payload.setdefault("service", service)
                if docker_ts:
                    payload.setdefault("ts", docker_ts)
                return payload
        except json.JSONDecodeError:
            pass

    return {
        "ts": docker_ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "service": service,
        "level": _guess_level(line),
        "msg": line,
    }


def _guess_level(line: str) -> str:
    upper = line.upper()
    if "TRACEBACK" in upper or "ERROR" in upper or "EXCEPTION" in upper:
        return "ERROR"
    if "WARN" in upper:
        return "WARNING"
    return "INFO"


def rotate_if_needed(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            path.replace(path.with_suffix(".jsonl.1"))
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output JSONL path")
    ap.add_argument("--since", default=None, help="docker log lookback, e.g. 30m")
    ap.add_argument("--once", action="store_true", help="snapshot then exit")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rotate_if_needed(out_path)

    cmd = ["docker", "compose", "logs", "--no-color", "--timestamps"]
    if not args.once:
        cmd.append("--follow")
    if args.since:
        cmd += ["--since", args.since]
    elif args.once:
        cmd += ["--tail", "500"]

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
    except FileNotFoundError:
        print("docker not found on PATH", file=sys.stderr)
        return 2

    def _stop(*_: Any) -> None:
        proc.terminate()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    written = 0
    with open(out_path, "a", encoding="utf-8") as fh:
        assert proc.stdout is not None
        for raw in proc.stdout:
            rec = parse_line(raw)
            if rec is None:
                continue
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            fh.flush()
            written += 1

    proc.wait()
    print(f"[logcollect] wrote {written} lines to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("COMPOSE_ANSI", "never")
    raise SystemExit(main())
