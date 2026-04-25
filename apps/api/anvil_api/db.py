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
        # Review-flow columns on images
        conn.execute(text(
            "ALTER TABLE images ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'new'"
        ))
        conn.execute(text(
            "ALTER TABLE images ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMP"
        ))
        conn.execute(text(
            "ALTER TABLE images ADD COLUMN IF NOT EXISTS submitted_by UUID"
        ))
        conn.execute(text(
            "ALTER TABLE images ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP"
        ))
        conn.execute(text(
            "ALTER TABLE images ADD COLUMN IF NOT EXISTS reviewed_by UUID"
        ))
        conn.execute(text(
            "ALTER TABLE images ADD COLUMN IF NOT EXISTS review_reason TEXT"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_images_review_status ON images(review_status)"
        ))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
