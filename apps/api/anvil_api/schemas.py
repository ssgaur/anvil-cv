"""Pydantic schemas (request / response models)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from anvil_api.models import (
    AnnotationSource,
    ClassPropertyType,
    GeometryType,
    ImageStatus,
    ModelVersionStatus,
    ReviewStatus,
)


# ---- Auth
class SignupIn(BaseModel):
    email: EmailStr
    name: str
    password: str = Field(min_length=4)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProvidersOut(BaseModel):
    password: bool
    signup: bool
    oauth: dict[str, bool]


class UserOut(BaseModel):
    id: UUID
    email: str
    name: str
    role: str
    created_at: datetime


# ---- Admin user management
class AdminUserOut(BaseModel):
    id: UUID
    email: str
    name: str
    role: str
    created_at: datetime
    has_password: bool
    oauth_providers: list[str] = []


class AdminUserCreateIn(BaseModel):
    email: EmailStr
    name: str
    password: str | None = Field(default=None, min_length=4)
    role: str = "labeler"


class AdminUserPatch(BaseModel):
    name: str | None = None
    role: str | None = None
    password: str | None = Field(default=None, min_length=4)


# ---- Projects
class ProjectIn(BaseModel):
    name: str
    description: str = ""


class ProjectOut(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str
    owner_id: UUID
    created_at: datetime
    dataset_count: int = 0
    image_count: int = 0


# ---- Datasets
class DatasetIn(BaseModel):
    name: str


class DatasetOut(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    slug: str
    created_at: datetime
    image_count: int = 0
    ready_count: int = 0


# ---- Images
class ImageOut(BaseModel):
    id: UUID
    dataset_id: UUID
    filename: str
    width: int
    height: int
    bytes: int
    status: ImageStatus
    error: str | None
    thumbnail_url: str | None
    image_url: str
    uncertainty: float | None
    annotation_count: int = 0
    review_status: ReviewStatus = ReviewStatus.NEW
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    comment_count: int = 0
    open_comment_count: int = 0


class ReviewActionIn(BaseModel):
    action: str  # "submit" | "approve" | "reject" | "reopen"
    reason: str | None = None  # required for reject; auto-attached as a general comment


# ---- Comments
class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    x: float | None = None
    y: float | None = None


class CommentPatch(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    resolved: bool | None = None


class CommentOut(BaseModel):
    id: UUID
    image_id: UUID
    author_id: UUID
    author_name: str
    body: str
    x: float | None
    y: float | None
    resolved: bool
    created_at: datetime


# ---- Classes
class ClassIn(BaseModel):
    name: str
    color: str = "#22c55e"
    geometry_type: GeometryType = GeometryType.POLYGON
    description: str = ""
    parent_id: UUID | None = None
    keypoint_schema: dict[str, Any] | None = None


class ClassOut(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    color: str
    geometry_type: GeometryType
    description: str
    annotation_count: int = 0
    parent_id: UUID | None = None
    keypoint_schema: dict[str, Any] | None = None


class ClassPatch(BaseModel):
    name: str | None = None
    color: str | None = None
    geometry_type: GeometryType | None = None
    description: str | None = None
    parent_id: UUID | None = None  # explicit null in body → set to root via separate param
    set_parent_root: bool = False  # when True, force parent_id NULL regardless of parent_id field
    keypoint_schema: dict[str, Any] | None = None


# ---- Class properties (per-class attribute schema)
class ClassPropertyIn(BaseModel):
    name: str
    type: ClassPropertyType = ClassPropertyType.SINGLE
    options: list[str] = []
    required: bool = False
    sort_order: int = 0


class ClassPropertyPatch(BaseModel):
    name: str | None = None
    type: ClassPropertyType | None = None
    options: list[str] | None = None
    required: bool | None = None
    sort_order: int | None = None


class ClassPropertyOut(BaseModel):
    id: UUID
    class_id: UUID
    name: str
    type: ClassPropertyType
    options: list[str]
    required: bool
    sort_order: int


# ---- Annotations
class AnnotationIn(BaseModel):
    class_id: UUID
    geometry_type: GeometryType
    geometry: dict[str, Any]
    source: AnnotationSource = AnnotationSource.MANUAL
    confidence: float | None = None
    properties: dict[str, Any] = {}


class AnnotationPatch(BaseModel):
    class_id: UUID | None = None
    geometry_type: GeometryType | None = None
    geometry: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None


class AnnotationOut(BaseModel):
    id: UUID
    image_id: UUID
    class_id: UUID
    geometry_type: GeometryType
    geometry: dict[str, Any]
    source: AnnotationSource
    confidence: float | None
    properties: dict[str, Any] = {}
    created_at: datetime


# ---- Predict (auto-annotate)
class PredictIn(BaseModel):
    points: list[list[float]] | None = None  # [[x,y],...] in image px
    point_labels: list[int] | None = None  # 1=foreground, 0=background
    box: list[float] | None = None  # [x1,y1,x2,y2]
    model_version_id: UUID | None = None  # if set, predict with this fine-tuned adapter


class PredictOut(BaseModel):
    polygon: list[list[float]]  # [[x,y], ...] simplified contour
    score: float
    bbox: list[float]  # [x,y,w,h]


# ---- Model versions (fine-tuned adapters)
class ModelVersionIn(BaseModel):
    name: str
    dataset_id: UUID  # which dataset's annotations to train on
    epochs: int = Field(default=20, ge=1, le=200)
    lr: float = Field(default=1e-4, gt=0, le=1e-2)
    rank: int = Field(default=8, ge=1, le=64)
    alpha: int = Field(default=16, ge=1, le=128)
    eval_split: float = Field(default=0.2, ge=0.0, le=0.5)


class ModelVersionOut(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    base_model: str
    dataset_id: UUID | None
    status: ModelVersionStatus
    progress: float
    error: str | None
    num_train_images: int
    num_train_annotations: int
    config: dict[str, Any]
    metrics: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None
    is_active: bool = False


class SetActiveModelIn(BaseModel):
    model_version_id: UUID | None  # null to clear (use base model)


class CompareIn(BaseModel):
    a_id: UUID | None  # null = base SAM2
    b_id: UUID | None  # null = base SAM2
    dataset_id: UUID | None = None  # restrict eval pool to this dataset (else any in project)
    n_samples: int = 12  # number of annotations to sample for the shared eval set
    seed: int | None = None
