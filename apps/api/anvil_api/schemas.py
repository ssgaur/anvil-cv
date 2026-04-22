"""Pydantic schemas (request / response models)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from anvil_api.models import AnnotationSource, GeometryType, ImageStatus


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


class UserOut(BaseModel):
    id: UUID
    email: str
    name: str
    created_at: datetime


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


# ---- Classes
class ClassIn(BaseModel):
    name: str
    color: str = "#22c55e"
    geometry_type: GeometryType = GeometryType.POLYGON
    description: str = ""


class ClassOut(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    color: str
    geometry_type: GeometryType
    description: str
    annotation_count: int = 0


# ---- Annotations
class AnnotationIn(BaseModel):
    class_id: UUID
    geometry_type: GeometryType
    geometry: dict[str, Any]
    source: AnnotationSource = AnnotationSource.MANUAL
    confidence: float | None = None


class AnnotationOut(BaseModel):
    id: UUID
    image_id: UUID
    class_id: UUID
    geometry_type: GeometryType
    geometry: dict[str, Any]
    source: AnnotationSource
    confidence: float | None
    created_at: datetime


# ---- Predict (auto-annotate)
class PredictIn(BaseModel):
    points: list[list[float]] | None = None  # [[x,y],...] in image px
    point_labels: list[int] | None = None  # 1=foreground, 0=background
    box: list[float] | None = None  # [x1,y1,x2,y2]


class PredictOut(BaseModel):
    polygon: list[list[float]]  # [[x,y], ...] simplified contour
    score: float
    bbox: list[float]  # [x,y,w,h]
