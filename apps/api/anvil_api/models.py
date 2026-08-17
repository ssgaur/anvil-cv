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


class ShowcaseVideo(SQLModel, table=True):
    """A local video prepared for the SAM3 inference showcase."""

    __tablename__ = "showcase_videos"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    filename: str = Field(max_length=255)
    source_key: str = Field(max_length=1000)
    playback_key: str | None = Field(default=None, max_length=1000)
    poster_key: str | None = Field(default=None, max_length=1000)
    bytes: int = Field(default=0)
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    duration_s: float | None = None
    frame_count: int | None = None
    codec: str | None = Field(default=None, max_length=64)
    # uploaded | preparing | ready | failed
    status: str = Field(default="uploaded", index=True, max_length=32)
    error: str | None = Field(default=None, max_length=2000)
    uploaded_by: UUID | None = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class ShowcaseRemovalJob(SQLModel, table=True):
    """A derived edit; the original ShowcaseVideo is never modified."""

    __tablename__ = "showcase_removal_jobs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="showcase_videos.id", index=True)
    concept: str = Field(max_length=255)
    start_s: float
    end_s: float
    seed_s: float
    selected_score: float | None = None
    mask_rle: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # queued | tracking | inpainting | encoding | completed | failed
    status: str = Field(default="queued", index=True, max_length=32)
    stage: str = Field(default="queued", max_length=64)
    progress: float = Field(default=0.0)
    active_seconds: float = Field(default=0.0)
    detail: str | None = Field(default=None, max_length=1000)
    output_key: str | None = Field(default=None, max_length=1000)
    error: str | None = Field(default=None, max_length=4000)
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
