# Observability & Diagnostics

Anvil runs as six containers (`api`, `ml`, `worker`, `web`, `postgres`, `minio`).
When something breaks, the failure is usually visible in one container while the
*symptom* shows up in another. This page describes the cumulative logging and
self-diagnosing tooling that exists so nobody — human or agent — has to copy an
error message out of a browser to get it fixed.

## The one command to run first

```bash
python3 scripts/diagnose.py
```

It reports container states (including OOM kills), service health, memory
pressure, failed images, and recent incidents — each with a concrete fix.

```bash
python3 scripts/diagnose.py --json   # machine-readable, for an LLM agent
```

Exit code is `0` when healthy and `1` when there are critical/error findings, so
it can gate CI or a watchdog.

Example output when the ML service is down:

```
  [CRITICAL] ml_unreachable
    what : ML service is not responding on :8001, so embedding and auto-annotate cannot work.
    fix  : Run `docker compose up -d ml` then `docker compose logs --tail 50 ml`.
```

## Cumulative log across all containers

Each service logs to its own stdout, so `docker compose logs` must be read once
per service and merged by hand. The collector solves that:

```bash
python3 scripts/logcollect.py            # follow → logs/anvil.jsonl
python3 scripts/logcollect.py --since 30m
python3 scripts/logcollect.py --once     # snapshot, then exit
```

Every line becomes one JSON object tagged with its originating `service`.
Structured logs from `api` and `ml` are merged field-for-field, so they stay
queryable:

```bash
grep '"level":"ERROR"' logs/anvil.jsonl | tail -20
jq -c 'select(.code) | {ts,service,code,remediation}' logs/anvil.jsonl
```

The file rotates at 32MB to `logs/anvil.jsonl.1`.

## Incidents: errors that explain themselves

`apps/api/anvil_api/diagnostics.py` classifies exceptions into named failure
modes. Every incident carries a stable `code`, a `probable_cause`, and a
`remediation`, so the log line *is* the fix instruction.

```json
{
  "level": "ERROR",
  "code": "ml_service_died",
  "component": "api",
  "operation": "embed",
  "detail": "RemoteProtocolError: Server disconnected without sending a response.",
  "probable_cause": "The ML service accepted the request then died mid-flight ... almost always the SAM2 image encoder being OOM-killed (container exit code 137).",
  "remediation": "Check `docker inspect anvil-cv-ml-1 --format '{{.State.OOMKilled}}'`. If true, raise the Docker/Rancher VM memory to at least 8GB ...",
  "ctx_image_id": "8157bcaf-...",
  "service": "api"
}
```

Current failure modes:

| Code | Meaning |
| --- | --- |
| `ml_service_died` | ML accepted the request then died mid-flight (usually OOM) |
| `ml_service_unreachable` | ML not accepting connections at all |
| `ml_service_timeout` | ML too slow — under-provisioned CPU or swapping |
| `ml_service_error` | ML returned an HTTP error status |
| `storage_error` | Image/embedding read/write failed (volume or disk) |
| `ml_memory_pressure` | ML above 85% of its memory limit; OOM is imminent |
| `unknown_error` | Unclassified — add a classifier when it recurs |

Add new modes in `diagnostics.py` by defining an `Incident` and extending
`classify()`.

## Health endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness only |
| `GET /ready` | Liveness + database ping |
| `GET /diagnostics` | Full self-diagnosis: DB, ML, failed images, incidents, fixes |
| `GET /metrics` | Prometheus text exposition |

`GET /diagnostics` is deliberately unauthenticated: it exposes no user data and
must stay reachable when auth or the database is the thing that is broken.

The ML service reports its own memory in `GET /health` on :8001:

```json
{"status":"ok","device":"cpu","memory_used_mb":320.1,"memory_limit_mb":9936.0,"memory_used_pct":3.2}
```

## Memory sizing (the most common local failure)

SAM2 `base_plus` peaks around **2 GB** while embedding a single image, on top of
Postgres, MinIO, Redis, api, worker and web sharing the same VM.

| VM memory | Result |
| --- | --- |
| 4 GB | ML gets OOM-killed during embedding (exit 137) |
| 8 GB | Works |
| 10 GB + 6-8 CPUs | Recommended |

Because an OOM kill is delivered by the kernel, the dying process can never log
it. That is why the ML service logs memory on **every** request and warns above
85% — the last line before the kill is the evidence.

## Watchdog usage for an agent

Point a background agent at the cumulative log and the diagnose script:

```bash
python3 scripts/logcollect.py &          # keep the stream fresh
watch -n 30 'python3 scripts/diagnose.py || true'
```

Any finding includes the exact remediation command, so an agent can act without
reproducing the failure.
