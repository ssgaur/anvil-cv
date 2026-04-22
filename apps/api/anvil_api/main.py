from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from anvil_api import __version__
from anvil_api.config import settings

app = FastAPI(title="Anvil API", version=__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "anvil-api", "docs": "/docs"}
