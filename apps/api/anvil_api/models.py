"""SQLModel domain models for Anvil v0.

Single-tenant for now (no Org). v0 entities:
  User, Project, Dataset, Image, Class, Annotation
"""
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, String
from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------- Enums ----------
class ImageStatus(StrEnum):
    UPLOADED = "uploaded"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class GeometryType(StrEnum):
    POLYGON = "polygon"
    BBOX = "bbox"
    BRUSH = "brush"  # rasterized → polygon on save
    POLYLINE = "polyline"   # open polyline; geometry: {"points":[[x,y],...]}
    KEYPOINTS = "keypoints"  # geometry: {"points":[[x,y,v],...]} v∈{0,1,2}; schema on Class
    OBB = "obb"             # rotated bbox; geometry: {"cx","cy","w","h","angle_rad"}


class AnnotationSource(StrEnum):
    MANUAL = "manual"
    MODEL = "model"
    IMPORTED = "imported"


class GeneratedBy(StrEnum):
    """Which system actually produced a mask.

    `AnnotationSource` only says manual/model/imported. For training we need to
    know *which* model, so we can measure per-model acceptance rates and avoid
    training a model on its own unreviewed output.
    """

    HUMAN = "human"
    SAM2 = "sam2"
    SAM3 = "sam3"
    IMPORTED = "imported"


class QueryStatus(StrEnum):
    """Lifecycle of one (image, concept) pair.

    `VERIFIED_ABSENT` is the critical one: it is the only way to record a
    trustworthy negative example. An empty instance list on its own means
    "nobody has looked yet" and must never be exported as a negative.
    """

    UNANNOTATED = "unannotated"
    IN_PROGRESS = "in_progress"
    VERIFIED_PRESENT = "verified_present"
    VERIFIED_ABSENT = "verified_absent"


class ModelVersionStatus(StrEnum):
    PENDING = "pending"
    TRAINING = "training"
    READY = "ready"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    """Per-image labeling lifecycle (V7-style)."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class UserRole(StrEnum):
    """Global user role. Single tenant; no per-project ACLs in v0.

    Hierarchy (most → least privileged):
      admin    — manage users, create/delete projects, train/delete models
      reviewer — manage taxonomy (datasets, classes), approve/reject reviews
      labeler  — annotate, submit for review
      viewer   — read-only across the instance
    """

    ADMIN = "admin"
    REVIEWER = "reviewer"
    LABELER = "labeler"
    VIEWER = "viewer"


class ClassPropertyType(StrEnum):
    """How a per-class attribute is captured for each annotation.

    - SINGLE: one-of (radio / dropdown), value is a string from `options`
    - MULTI:  any-of (checkboxes), value is a list[str] (subset of `options`)
    - TEXT:   free-form string
    """

    SINGLE = "single"
    MULTI = "multi"
    TEXT = "text"


# ---------- Tables ----------
class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=255)
    name: str = Field(max_length=255)
    # Nullable because OAuth-only users have no local password.
    password_hash: str | None = Field(default=None)
    role: UserRole = Field(
        default=UserRole.LABELER,
        sa_column=Column(String, nullable=False, server_default="labeler", index=True),
    )
    # Bumped on password reset / logout-everywhere to invalidate all
    # outstanding access JWTs immediately (refresh tokens are revoked
    # separately in their own table).
    token_version: int = Field(default=0, sa_column_kwargs={"nullable": False, "server_default": "0"})
    created_at: datetime = Field(default_factory=utcnow)

    projects: list["Project"] = Relationship(back_populates="owner")
    oauth_identities: list["UserOAuthIdentity"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete"},
    )


class UserOAuthIdentity(SQLModel, table=True):
    """Linked external identity (Google, GitHub, ...) for a User.

    A User may have multiple identities (one per provider). Linking by email
    is allowed only when the provider returns a verified email.
    """

    __tablename__ = "user_oauth_identities"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    provider: str = Field(max_length=32, index=True)  # "google" | "github"
    subject: str = Field(max_length=255)  # provider-stable user id
    email: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=utcnow)

    user: User = Relationship(back_populates="oauth_identities")


class UserRefreshToken(SQLModel, table=True):
    """Rotating refresh-token chain for cookie sessions.

    Storage stores only an HMAC-SHA256 of the raw token (keyed by JWT_SECRET).
    Rotation: each refresh issues a new row whose `parent_id` is the row that
    was just consumed; the consumed row's `used_at` is set atomically. Any
    second use of an already-used token revokes the entire `family_id`
    (chain), which is the standard refresh-token-reuse-detection pattern.
    A small grace window (`refresh_reuse_grace_seconds`) tolerates benign
    multi-tab races without flagging them as theft.
    """

    __tablename__ = "user_refresh_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    family_id: UUID = Field(index=True)
    parent_id: UUID | None = Field(default=None, index=True)
    token_hash: str = Field(max_length=128, index=True, unique=True)
    user_agent: str | None = Field(default=None, max_length=255)
    ip: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    used_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)
    revoked_reason: str | None = Field(default=None, max_length=64)


class PasswordResetToken(SQLModel, table=True):
    """Single-use password-reset token. Storage holds an HMAC-SHA256 of the
    raw token (keyed by JWT_SECRET). The raw token is delivered to the user
    via email and presented back to /password-reset/confirm exactly once."""

    __tablename__ = "password_reset_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(max_length=128, index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    used_at: datetime | None = Field(default=None)
    ip: str | None = Field(default=None, max_length=64)


class InstanceState(SQLModel, table=True):
    """Singleton-style key/value table for instance-wide flags (e.g. setup
    completion marker). Avoids a dedicated migration for every new flag."""

    __tablename__ = "instance_state"

    key: str = Field(primary_key=True, max_length=64)
    value: str | None = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow)


class Project(SQLModel, table=True):
    __tablename__ = "projects"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=255)
    slug: str = Field(unique=True, index=True, max_length=255)
    description: str = Field(default="")
    owner_id: UUID = Field(foreign_key="users.id", index=True)
    active_model_version_id: UUID | None = Field(default=None)  # FK added later (avoid circular)
    created_at: datetime = Field(default_factory=utcnow)

    owner: User = Relationship(back_populates="projects")
    datasets: list["Dataset"] = Relationship(back_populates="project", sa_relationship_kwargs={"cascade": "all, delete"})
    classes: list["Class"] = Relationship(back_populates="project", sa_relationship_kwargs={"cascade": "all, delete"})


class Dataset(SQLModel, table=True):
    __tablename__ = "datasets"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", index=True)
    name: str = Field(max_length=255)
    slug: str = Field(max_length=255)
    created_at: datetime = Field(default_factory=utcnow)

    project: Project = Relationship(back_populates="datasets")
    images: list["Image"] = Relationship(back_populates="dataset", sa_relationship_kwargs={"cascade": "all, delete"})


class Image(SQLModel, table=True):
    __tablename__ = "images"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    dataset_id: UUID = Field(foreign_key="datasets.id", index=True)
    filename: str = Field(max_length=512)
    storage_key: str = Field(max_length=1024)  # provider-relative key
    embedding_key: str | None = Field(default=None, max_length=1024)
    thumbnail_key: str | None = Field(default=None, max_length=1024)
    width: int = 0
    height: int = 0
    bytes: int = 0
    status: ImageStatus = Field(default=ImageStatus.UPLOADED, index=True)
    error: str | None = None
    uncertainty: float | None = Field(default=None, index=True)  # for active learning
    review_status: ReviewStatus = Field(default=ReviewStatus.NEW, index=True)
    submitted_at: datetime | None = None
    submitted_by: UUID | None = Field(default=None, foreign_key="users.id")
    reviewed_at: datetime | None = None
    reviewed_by: UUID | None = Field(default=None, foreign_key="users.id")
    review_reason: str | None = None  # last reject reason (if any)
    uploaded_at: datetime = Field(default_factory=utcnow)

    dataset: Dataset = Relationship(back_populates="images")
    annotations: list["Annotation"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete"})
    comments: list["Comment"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete"})


class Class(SQLModel, table=True):
    __tablename__ = "classes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", index=True)
    name: str = Field(max_length=255)
    color: str = Field(max_length=16)  # #RRGGBB
    geometry_type: GeometryType = Field(default=GeometryType.POLYGON)
    description: str = Field(default="")
    # Optional parent for taxonomy hierarchy. NULL = root. Cycle prevention
    # and same-project enforcement live in routes/classes.py.
    parent_id: UUID | None = Field(default=None, foreign_key="classes.id", index=True)
    # Per-class keypoint template (only used when geometry_type=KEYPOINTS).
    # Shape: {"keypoints":[{"name":..,"color":?},...], "edges":[[i,j],...]}
    # 0-based indices. Treated as immutable once any keypoint annotation references this class.
    keypoint_schema: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)

    project: Project = Relationship(back_populates="classes")


class ModelVersion(SQLModel, table=True):
    __tablename__ = "model_versions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", index=True)
    name: str = Field(max_length=255)
    base_model: str = Field(default="sam2.1_hiera_b+", max_length=128)
    dataset_id: UUID | None = Field(default=None, foreign_key="datasets.id")
    adapter_key: str | None = Field(default=None, max_length=1024)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    num_train_images: int = 0
    num_train_annotations: int = 0
    status: ModelVersionStatus = Field(default=ModelVersionStatus.PENDING, index=True)
    error: str | None = None
    progress: float = 0.0  # 0..1
    created_by: UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None


class Annotation(SQLModel, table=True):
    __tablename__ = "annotations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    image_id: UUID = Field(foreign_key="images.id", index=True)
    class_id: UUID = Field(foreign_key="classes.id", index=True)
    geometry_type: GeometryType
    geometry: dict[str, Any] = Field(sa_column=Column(JSON))
    # geometry shape:
    #   polygon: {"points": [[x,y], ...]}
    #   bbox:    {"x":, "y":, "w":, "h":}
    source: AnnotationSource = Field(default=AnnotationSource.MANUAL)
    confidence: float | None = None
    properties: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # properties shape: {<class_property_id>: <value>}
    #   single → str
    #   multi  → list[str]
    #   text   → str

    # ---- Concept link (SAM3 / open-vocabulary) ----
    # Null for classic SAM2 class-based annotations, so existing rows are valid.
    query_id: UUID | None = Field(default=None, foreign_key="queries.id", index=True)

    # ---- Mask fidelity ----
    # Compressed COCO RLE of the exact mask. Polygons are a lossy simplification
    # (SAM2 and SAM3 both run approxPolyDP), which is fine for editing but bad
    # as a training target. Trainers should prefer mask_rle when present.
    mask_rle: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))

    # ---- Provenance (Phase 22) ----
    # Stored as TEXT rather than a native PG enum: enums require ALTER TYPE to
    # evolve, and create_all() vs ALTER TABLE would otherwise produce different
    # column types on fresh vs migrated databases. Matches User.role.
    generated_by: GeneratedBy = Field(
        default=GeneratedBy.HUMAN,
        sa_column=Column(String, nullable=False, server_default="human", index=True),
    )
    model_name: str | None = Field(default=None, max_length=64)
    model_version_id: UUID | None = Field(default=None, index=True)
    model_score: float | None = None
    human_verified: bool = Field(default=False, index=True)
    human_corrected: bool = Field(default=False, index=True)
    # The model's untouched output, kept so we can measure how much correction
    # a prediction needed and never lose the original on edit (Phase 4).
    original_geometry: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    original_mask_rle: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )

    created_by: UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class Concept(SQLModel, table=True):
    """A canonical text phrase used as an open-vocabulary query (SAM3).

    Kept separate from `Class` on purpose. A Class is a fixed taxonomy entry
    with a geometry type and a colour; a Concept is a *phrase* you can ask a
    model about ("red car", "leaf blight"). Linking the two is optional so a
    project can use SAM2 classes, SAM3 concepts, or both.
    """

    __tablename__ = "concepts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", index=True)
    # The exact phrase sent to the model and exported as the training category.
    canonical_phrase: str = Field(max_length=255, index=True)
    # Alternative wordings kept for search/UX only. Deliberately NOT injected
    # into training data (Phase 23) — that would silently change the dataset.
    aliases: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    description: str | None = Field(default=None, max_length=1000)
    # Optional bridge to the SAM2 taxonomy so both models can share a label.
    class_id: UUID | None = Field(default=None, foreign_key="classes.id", index=True)
    color: str | None = Field(default=None, max_length=7)
    created_by: UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class Query(SQLModel, table=True):
    """One (image, concept) annotation unit — the core SAM3 training record.

    SAM3 does not learn from "image + object" alone; it learns from
    "image + phrase -> every matching instance, and whether that set is
    complete". Those completeness flags cannot be inferred after the fact,
    which is why they live here rather than being derived at export time.
    """

    __tablename__ = "queries"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    image_id: UUID = Field(foreign_key="images.id", index=True)
    concept_id: UUID = Field(foreign_key="concepts.id", index=True)
    # Snapshot of the phrase actually used, so later concept renames do not
    # rewrite history for datasets already exported.
    text: str = Field(max_length=255)

    status: QueryStatus = Field(
        default=QueryStatus.UNANNOTATED,
        sa_column=Column(
            String, nullable=False, server_default="unannotated", index=True
        ),
    )
    # True iff EVERY instance of this concept in this image is annotated.
    is_exhaustive: bool = Field(default=False)
    # True iff the masks cover all target pixels (not just some instances).
    is_pixel_exhaustive: bool = Field(default=False)

    # Which model produced the candidates, for acceptance-rate metrics.
    model_name: str | None = Field(default=None, max_length=64)
    model_version_id: UUID | None = Field(default=None)
    threshold: float | None = None

    created_by: UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class DatasetVersion(SQLModel, table=True):
    """Immutable snapshot of a dataset, so a training run is reproducible.

    Without this, a model trained "on dataset X" is unreproducible the moment
    anyone edits a mask. Once `frozen` is set the row must never be mutated;
    corrections create a new version instead.
    """

    __tablename__ = "dataset_versions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    dataset_id: UUID = Field(foreign_key="datasets.id", index=True)
    version: int = Field(index=True)
    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=1000)

    # Exact ID lists captured at freeze time: image_ids, annotation_ids,
    # query_ids, concept_ids. Stored rather than re-queried so the membership
    # cannot drift.
    snapshot: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # Denormalised counts + class/concept histograms for quick display.
    stats: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # Reproducible train/val/test assignment (image_id -> split).
    splits: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    frozen: bool = Field(default=True)
    created_by: UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class ClassProperty(SQLModel, table=True):
    """Per-class attribute schema (V7 calls these 'properties')."""

    __tablename__ = "class_properties"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    class_id: UUID = Field(foreign_key="classes.id", index=True)
    name: str = Field(max_length=255)
    type: ClassPropertyType = Field(default=ClassPropertyType.SINGLE)
    options: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    required: bool = Field(default=False)
    sort_order: int = Field(default=0, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class Comment(SQLModel, table=True):
    """Spatial or general comment pinned on an image (review threads)."""

    __tablename__ = "comments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    image_id: UUID = Field(foreign_key="images.id", index=True)
    author_id: UUID = Field(foreign_key="users.id", index=True)
    body: str = Field(max_length=4000)
    # If x or y is None → "general" comment (no pin on canvas).
    # Coordinates are in image pixels.
    x: float | None = None
    y: float | None = None
    resolved: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
