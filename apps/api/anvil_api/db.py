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
    from sqlalchemy import text
    # ALTER TYPE ... ADD VALUE must run outside a transaction block in PG.
    # Idempotent via IF NOT EXISTS (PG 12+).
    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        for v in ("POLYLINE", "KEYPOINTS", "OBB"):
            conn.execute(text(f"ALTER TYPE geometrytype ADD VALUE IF NOT EXISTS '{v}'"))
    # Lightweight forward-compat migrations (solo dev; replace with Alembic later)
    with engine.begin() as conn:
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
        # OAuth: relax password and add identities table + partial unique idx.
        conn.execute(text(
            "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_user_oauth_identities_provider_subject "
            "ON user_oauth_identities(provider, subject) "
            "WHERE provider IS NOT NULL AND subject IS NOT NULL"
        ))
        # RBAC: per-user global role (admin/reviewer/labeler/viewer).
        # Default = labeler so existing single-user deployments keep editing
        # access; bootstrap_admin promotes the configured admin email.
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'labeler'"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_users_role ON users(role)"
        ))
        # Per-user token version: bumped on password reset / logout-everywhere
        # to invalidate access JWTs immediately (claim "tv" must match column).
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0"
        ))
        # CHECK constraint on role — drop+add to make it idempotent across
        # schema changes.
        conn.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role"))
        conn.execute(text(
            "ALTER TABLE users ADD CONSTRAINT ck_users_role "
            "CHECK (role IN ('admin','reviewer','labeler','viewer'))"
        ))
        # Class hierarchy + keypoint schema (prod-class-hierarchy / prod-keypoint-tool).
        conn.execute(text(
            "ALTER TABLE classes ADD COLUMN IF NOT EXISTS parent_id UUID"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_classes_parent_id ON classes(parent_id)"
        ))
        conn.execute(text(
            "ALTER TABLE classes ADD COLUMN IF NOT EXISTS keypoint_schema JSONB"
        ))
        # Indexes for prod-image-filters (class filter via EXISTS, label-count sort).
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_annotations_image_class "
            "ON annotations(image_id, class_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_images_dataset_uploaded "
            "ON images(dataset_id, uploaded_at)"
        ))
        conn.execute(text(
            "ALTER TABLE showcase_removal_jobs "
            "ADD COLUMN IF NOT EXISTS active_seconds DOUBLE PRECISION "
            "NOT NULL DEFAULT 0"
        ))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
