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
        # --- Canonical training-ready schema (concepts / queries / provenance) ---
        # Concept link + mask fidelity + provenance on annotations. All nullable
        # or defaulted so existing SAM2 rows stay valid without a backfill.
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS query_id UUID"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS mask_rle JSONB"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS generated_by TEXT "
            "NOT NULL DEFAULT 'human'"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS model_name VARCHAR(64)"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS model_version_id UUID"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS model_score DOUBLE PRECISION"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS human_verified BOOLEAN "
            "NOT NULL DEFAULT FALSE"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS human_corrected BOOLEAN "
            "NOT NULL DEFAULT FALSE"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS original_geometry JSONB"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS original_mask_rle JSONB"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_annotations_query_id ON annotations(query_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_annotations_generated_by "
            "ON annotations(generated_by)"
        ))
        # Normalise enum-typed columns to TEXT. A database created by an
        # earlier create_all() may have native PG enums here (labels are
        # UPPERCASE names), while a migrated one has TEXT with lowercase
        # values. Converting makes both agree; it is a no-op once TEXT.
        conn.execute(text(
            "ALTER TABLE annotations ALTER COLUMN generated_by TYPE TEXT "
            "USING lower(generated_by::text)"
        ))
        conn.execute(text(
            "ALTER TABLE annotations ALTER COLUMN generated_by SET DEFAULT 'human'"
        ))
        conn.execute(text(
            "ALTER TABLE queries ALTER COLUMN status TYPE TEXT "
            "USING lower(status::text)"
        ))
        conn.execute(text(
            "ALTER TABLE queries ALTER COLUMN status SET DEFAULT 'unannotated'"
        ))
        # Backfill provenance for rows created before this column existed:
        # AnnotationSource.MODEL predates SAM3, so it can only have been SAM2.
        conn.execute(text(
            "UPDATE annotations SET generated_by = 'sam2' "
            "WHERE source = 'MODEL' AND generated_by = 'human'"
        ))
        conn.execute(text(
            "UPDATE annotations SET generated_by = 'imported' "
            "WHERE source = 'IMPORTED' AND generated_by = 'human'"
        ))
        # One concept phrase per project; one query per (image, concept).
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_concepts_project_phrase "
            "ON concepts(project_id, canonical_phrase)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_queries_image_concept "
            "ON queries(image_id, concept_id)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_dataset_versions_dataset_version "
            "ON dataset_versions(dataset_id, version)"
        ))


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
