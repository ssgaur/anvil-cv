from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from anvil_api import __version__
from anvil_api.auth import get_or_create_admin
from anvil_api.config import settings
from anvil_api.db import engine, init_db
from anvil_api.routes import (
    annotations,
    auth,
    classes,
    comments,
    datasets,
    images,
    models,
    projects,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with Session(engine) as s:
        get_or_create_admin(s)
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
app.include_router(projects.router, prefix=API)
app.include_router(datasets.router, prefix=API)
app.include_router(images.router, prefix=API)
app.include_router(classes.router, prefix=API)
app.include_router(annotations.router, prefix=API)
app.include_router(comments.router, prefix=API)
app.include_router(models.router, prefix=API)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "anvil-api", "docs": "/docs"}
