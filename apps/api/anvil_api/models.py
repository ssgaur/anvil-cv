"""SQLModel domain models for Anvil v0.

Single-tenant for now (no Org). v0 entities:
  User, Project, Dataset, Image, Class, Annotation
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Enums ----------
class ImageStatus(str, Enum):
    UPLOADED = "uploaded"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class GeometryType(str, Enum):
    POLYGON = "polygon"
    BBOX = "bbox"
    BRUSH = "brush"  # rasterized → polygon on save


class AnnotationSource(str, Enum):
    MANUAL = "manual"
    MODEL = "model"
    IMPORTED = "imported"


class ModelVersionStatus(str, Enum):
    PENDING = "pending"
    TRAINING = "training"
    READY = "ready"
    FAILED = "failed"


class ClassPropertyType(str, Enum):
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
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)

    projects: list["Project"] = Relationship(back_populates="owner")


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
    uploaded_at: datetime = Field(default_factory=utcnow)

    dataset: Dataset = Relationship(back_populates="images")
    annotations: list["Annotation"] = Relationship(sa_relationship_kwargs={"cascade": "all, delete"})


class Class(SQLModel, table=True):
    __tablename__ = "classes"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", index=True)
    name: str = Field(max_length=255)
    color: str = Field(max_length=16)  # #RRGGBB
    geometry_type: GeometryType = Field(default=GeometryType.POLYGON)
    description: str = Field(default="")
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
