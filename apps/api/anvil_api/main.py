from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from anvil_api import __version__
from anvil_api.auth import bootstrap_admin
from anvil_api.config import settings
from anvil_api.db import engine, init_db
from anvil_api.routes import (
    admin,
    annotations,
    auth,
    classes,
    comments,
    datasets,
    images,
    models,
    oauth,
    projects,
    setup,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "anvil-api", "docs": "/docs"}
