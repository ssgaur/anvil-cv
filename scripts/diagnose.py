#!/usr/bin/env python3
"""One-shot health + incident report for Anvil, designed to be read by an agent.

The goal is that nobody has to copy an error message out of a browser. Run this
and it reports what is broken, why, and the exact command to fix it.

It deliberately depends only on the stdlib + the `docker` CLI so it works even
when the API is down.

Usage:
    python3 scripts/diagnose.py            # human-readable report
    python3 scripts/diagnose.py --json     # machine-readable for an agent
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOG_FILE = ROOT / "logs" / "anvil.jsonl"

API_URL = "http://localhost:8000"
ML_URL = "http://localhost:8001"


def sh(args: list[str], timeout: int = 20) -> str:
    try:
        return subprocess.run(
            args, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def http_json(url: str, timeout: int = 5) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


def container_states() -> list[dict[str, Any]]:
    """Per-container status, including the OOM flag that explains silent deaths."""
    if not shutil.which("docker"):
        return []
    raw = sh(["docker", "compose", "ps", "-a", "--format", "json"])
    if not raw:
        return []

    entries: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.extend(parsed if isinstance(parsed, list) else [parsed])

    out: list[dict[str, Any]] = []
    for e in entries:
        name = e.get("Name") or ""
        info: dict[str, Any] = {
            "service": e.get("Service") or name,
            "state": e.get("State") or "",
            "status": e.get("Status") or "",
            "exit_code": e.get("ExitCode"),
        }
        if name:
            fmt = "{{.State.OOMKilled}}|{{.State.ExitCode}}|{{.State.Error}}"
            detail = sh(["docker", "inspect", name, "--format", fmt])
            if detail:
                oom, code, err = (detail.split("|", 2) + ["", "", ""])[:3]
                info["oom_killed"] = oom == "true"
                info["exit_code"] = code
                if err:
                    info["error"] = err
        out.append(info)
    return out


def recent_incidents(limit: int = 20, window_minutes: int = 30) -> list[dict[str, Any]]:
    """Incidents from the aggregated log, newest last.

    Bounded by a time window so long-resolved failures don't get reported as if
    they were current — a diagnostic tool that cries wolf gets ignored.
    """
    if not LOG_FILE.exists():
        return []
    cutoff = time.time() - window_minutes * 60
    found: list[dict[str, Any]] = []
    try:
        with open(LOG_FILE, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not (rec.get("code") or rec.get("level") == "ERROR"):
                    continue
                if _ts_epoch(rec.get("ts")) < cutoff:
                    continue
                found.append(rec)
    except OSError:
        return []
    return found[-limit:]


def _ts_epoch(ts: Any) -> float:
    """Parse a log timestamp to epoch seconds; unparseable -> 0 (treated as old)."""
    if not isinstance(ts, str):
        return 0.0
    cleaned = ts.replace("Z", "+0000")
    # Docker's RFC3339Nano has 9 fractional digits; %f accepts at most 6.
    if "." in cleaned and "+" in cleaned:
        head, rest = cleaned.split(".", 1)
        frac, tz = rest.split("+", 1)
        cleaned = f"{head}.{frac[:6]}+{tz}"
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(cleaned, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def analyze(report: dict[str, Any]) -> list[dict[str, str]]:
    """Turn raw signals into prioritized, actionable findings."""
    findings: list[dict[str, str]] = []

    for c in report["containers"]:
        if c.get("oom_killed"):
            findings.append({
                "severity": "critical",
                "code": "container_oom_killed",
                "detail": f"Container '{c['service']}' was OOM-killed (exit 137).",
                "remediation": (
                    "Raise the Docker/Rancher VM memory to at least 8GB (10GB "
                    "recommended) and 6+ CPUs, then run `docker compose up -d`."
                ),
            })
        elif c.get("state") not in ("running", "exited", "") and c.get("state"):
            findings.append({
                "severity": "warning",
                "code": "container_not_running",
                "detail": f"Container '{c['service']}' is in state '{c['state']}'.",
                "remediation": f"Run `docker compose up -d {c['service']}` and check its logs.",
            })

    if report["services"]["ml"] is None:
        findings.append({
            "severity": "critical",
            "code": "ml_unreachable",
            "detail": "ML service is not responding on :8001, so embedding and auto-annotate cannot work.",
            "remediation": "Run `docker compose up -d ml` then `docker compose logs --tail 50 ml`.",
        })
    else:
        pct = report["services"]["ml"].get("memory_used_pct")
        if isinstance(pct, int | float) and pct >= 85:
            findings.append({
                "severity": "warning",
                "code": "ml_memory_pressure",
                "detail": f"ML service is at {pct}% of its memory limit and may be OOM-killed.",
                "remediation": "Raise the Docker/Rancher VM memory before running SAM2 work.",
            })

    if report["services"]["api"] is None:
        findings.append({
            "severity": "critical",
            "code": "api_unreachable",
            "detail": "API is not responding on :8000.",
            "remediation": "Run `docker compose up -d api` then `docker compose logs --tail 50 api`.",
        })

    if not LOG_FILE.exists():
        findings.append({
            "severity": "info",
            "code": "no_aggregated_log",
            "detail": "No cumulative log found at logs/anvil.jsonl.",
            "remediation": "Start the collector: `python3 scripts/logcollect.py &`.",
        })

    ml_up = report["services"]["ml"] is not None
    for inc in report["incidents"]:
        if not (inc.get("code") and inc.get("remediation")):
            continue
        # An ML incident from 10 minutes ago is history if ML is answering now.
        # Report it as context, not as an active failure, so the exit code and
        # the operator's attention stay meaningful.
        resolved = ml_up and str(inc.get("code", "")).startswith("ml_")
        findings.append({
            "severity": "info" if resolved else "error",
            "code": str(inc["code"]) + (" (resolved)" if resolved else ""),
            "detail": str(inc.get("detail") or inc.get("msg") or "")[:300],
            "remediation": (
                "No action needed — the service recovered. Historical context only."
                if resolved else str(inc["remediation"])
            ),
        })

    return findings


def build_report(window_minutes: int = 30) -> dict[str, Any]:
    report: dict[str, Any] = {
        "containers": container_states(),
        "services": {
            "api": http_json(f"{API_URL}/health"),
            "api_ready": http_json(f"{API_URL}/ready"),
            "ml": http_json(f"{ML_URL}/health"),
        },
        "incidents": recent_incidents(window_minutes=window_minutes),
        "incident_window_minutes": window_minutes,
        "aggregated_log": str(LOG_FILE) if LOG_FILE.exists() else None,
    }
    report["findings"] = analyze(report)
    report["healthy"] = not any(
        f["severity"] in ("critical", "error") for f in report["findings"]
    )
    return report


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 68)
    lines.append("ANVIL DIAGNOSTICS")
    lines.append("=" * 68)

    lines.append("\nContainers:")
    if report["containers"]:
        for c in report["containers"]:
            flag = "  [OOM-KILLED]" if c.get("oom_killed") else ""
            lines.append(f"  {c['service']:<16} {c.get('status') or c.get('state')}{flag}")
    else:
        lines.append("  (docker unavailable or no compose project running)")

    lines.append("\nServices:")
    ml = report["services"]["ml"]
    api = report["services"]["api"]
    lines.append(f"  api  : {'up' if api else 'DOWN'}")
    if ml:
        mem = ""
        if ml.get("memory_used_pct") is not None:
            mem = f" mem={ml.get('memory_used_mb')}MB/{ml.get('memory_limit_mb')}MB ({ml.get('memory_used_pct')}%)"
        lines.append(f"  ml   : up device={ml.get('device')}{mem}")
    else:
        lines.append("  ml   : DOWN")

    findings = report["findings"]
    if not findings:
        lines.append("\nNo problems detected.")
    else:
        lines.append(f"\nFindings ({len(findings)}):")
        for f in findings:
            lines.append(f"\n  [{f['severity'].upper()}] {f['code']}")
            lines.append(f"    what : {f['detail']}")
            lines.append(f"    fix  : {f['remediation']}")

    if report["aggregated_log"]:
        lines.append(f"\nCumulative log: {report['aggregated_log']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument(
        "--window", type=int, default=30,
        help="only report incidents from the last N minutes (default 30)",
    )
    args = ap.parse_args()

    report = build_report(window_minutes=args.window)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
