from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlmodel import Session, select

from anvil_api import __version__
from anvil_api.auth import bootstrap_admin
from anvil_api.config import settings
from anvil_api.csrf import CSRFMiddleware
from anvil_api.db import engine, init_db
from anvil_api.observability import (
    MetricsMiddleware,
    configure_logging,
    render_metrics,
)
from anvil_api.routes import (
    admin,
    annotations,
    auth,
    bulk,
    classes,
    comments,
    datasets,
    images,
    models,
    oauth,
    password_reset,
    projects,
    refresh,
    setup,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging(level="INFO")
    init_db()
    with Session(engine) as s:
        bootstrap_admin(s)
        # If an admin already exists (bootstrap or otherwise), mark setup
        # as completed so the public /setup endpoint stays closed even after
        # admin deletion (admin-recovery is an out-of-band operation).
        from datetime import datetime as _dt

        from sqlmodel import select as _select

        from anvil_api.models import InstanceState, User, UserRole
        admin_seen = s.exec(
            _select(User.id).where(User.role == UserRole.ADMIN.value).limit(1)
        ).first()
        if admin_seen is not None and s.get(InstanceState, "setup_completed_at") is None:
            s.merge(InstanceState(key="setup_completed_at", value=_dt.utcnow().isoformat()))
            s.commit()
    yield


app = FastAPI(title="Anvil API", version=__version__, lifespan=lifespan)

# CSRF must run AFTER CORS so OPTIONS preflights are handled by CORS.
# Starlette runs middlewares in reverse-add order, so add CSRF first.
app.add_middleware(CSRFMiddleware)
app.add_middleware(MetricsMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

API = "/api"
app.include_router(auth.router, prefix=API)
app.include_router(oauth.router, prefix=API)
app.include_router(projects.router, prefix=API)
app.include_router(datasets.router, prefix=API)
app.include_router(images.router, prefix=API)
app.include_router(classes.router, prefix=API)
app.include_router(annotations.router, prefix=API)
app.include_router(comments.router, prefix=API)
app.include_router(models.router, prefix=API)
app.include_router(admin.router, prefix=API)
app.include_router(setup.router, prefix=API)
app.include_router(refresh.router, prefix=API)
app.include_router(password_reset.router, prefix=API)
app.include_router(bulk.router, prefix=API)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/ready")
def ready() -> Response:
    """Liveness vs readiness: /health is liveness; /ready also pings the DB."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        return Response(
            content=f'{{"status":"not_ready","error":"{type(e).__name__}"}}',
            media_type="application/json", status_code=503,
        )
    return Response(content='{"status":"ready"}', media_type="application/json")


@app.get("/diagnostics")
async def diagnostics_report() -> dict[str, object]:
    """Self-describing health report: what is broken, why, and how to fix it.

    Unauthenticated on purpose — it exposes no user data, and it has to stay
    reachable exactly when auth or the DB is the thing that is broken.
    """
    from anvil_api import diagnostics as diag
    from anvil_api.ml_client import ml_client

    findings: list[dict[str, str]] = []

    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        db_ok = False
        findings.append({
            "severity": "critical",
            "code": "database_unreachable",
            "detail": f"{type(e).__name__}: {e}"[:300],
            "remediation": (
                "Run `docker compose ps postgres` and "
                "`docker compose logs --tail 50 postgres`; confirm DATABASE_URL."
            ),
        })

    ml_info: dict[str, object] | None = None
    try:
        ml_info = await ml_client.health()
    except Exception as e:
        incident = diag.classify(e)
        findings.append({
            "severity": "critical",
            "code": incident.code,
            "detail": f"{type(e).__name__}: {e}"[:300],
            "remediation": incident.remediation,
        })

    if isinstance(ml_info, dict):
        pct = ml_info.get("memory_used_pct")
        if isinstance(pct, int | float) and pct >= 85:
            findings.append({
                "severity": "warning",
                "code": "ml_memory_pressure",
                "detail": f"ML service is at {pct}% of its memory limit.",
                "remediation": (
                    "Raise the Docker/Rancher VM memory to at least 8GB "
                    "(10GB recommended) before running SAM2 work."
                ),
            })

    stuck: list[dict[str, str]] = []
    if db_ok:
        from anvil_api.models import Image, ImageStatus
        with Session(engine) as s:
            failed = s.exec(
                select(Image).where(Image.status == ImageStatus.FAILED).limit(10)
            ).all()
            for img in failed:
                stuck.append({
                    "image_id": str(img.id),
                    "dataset_id": str(img.dataset_id),
                    "filename": img.filename,
                    "error": img.error or "",
                })
        if stuck:
            findings.append({
                "severity": "error",
                "code": "images_failed_embedding",
                "detail": f"{len(stuck)} image(s) have no embedding and cannot be trained on.",
                "remediation": (
                    "Fix the underlying ML failure above, then re-upload the "
                    "affected images. Check `logs/anvil.jsonl` for the incident."
                ),
            })

    return {
        "healthy": not any(f["severity"] in ("critical", "error") for f in findings),
        "database": "up" if db_ok else "down",
        "ml": ml_info or "down",
        "findings": findings,
        "failed_images": stuck,
        "recent_incidents": diag.recent(20),
    }


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4")


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "anvil-api", "docs": "/docs"}
