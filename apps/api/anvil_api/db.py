"""DB engine + session helpers + create_all bootstrap (v0; switch to Alembic later)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from anvil_api.config import settings

engine = create_engine(settings.database_url, echo=False, pool_pre_ping=True)


def init_db() -> None:
    # Import models so SQLModel knows about them
    from anvil_api import models  # noqa: F401
    SQLModel.metadata.create_all(engine)
    # Lightweight forward-compat migrations (solo dev; replace with Alembic later)
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text(
            "ALTER TABLE projects ADD COLUMN IF NOT EXISTS active_model_version_id UUID"
        ))
        # Per-annotation user-defined properties (class-property values)
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS properties JSONB NOT NULL DEFAULT '{}'::jsonb"
        ))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
