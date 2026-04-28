// Domain types mirroring API schemas

export type UserRole = "admin" | "reviewer" | "labeler" | "viewer";

export interface User {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  created_at: string;
}

export interface AdminUser extends User {
  oauth_providers: string[];
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description: string;
  owner_id: string;
  created_at: string;
  dataset_count: number;
  image_count: number;
  active_model_version_id?: string | null;
}

export type ModelVersionStatus = "pending" | "training" | "ready" | "failed";

export interface ModelVersion {
  id: string;
  project_id: string;
  name: string;
  base_model: string;
  dataset_id: string | null;
  status: ModelVersionStatus;
  progress: number;
  error: string | null;
  num_train_images: number;
  num_train_annotations: number;
  config: Record<string, unknown>;
  metrics: Record<string, unknown>;
  created_at: string;
  completed_at: string | null;
  is_active: boolean;
}

export interface CompareSide {
  id: string | null;
  name: string;
  mean_iou: number;
  per_class_iou: Record<string, number>;
}

export interface CompareResult {
  a: CompareSide;
  b: CompareSide;
  delta_mean_iou: number;
  per_class: { class_name: string; a_iou: number | null; b_iou: number | null; delta: number | null }[];
  per_item: { image_path: string; class_name: string; a_iou: number | null; b_iou: number | null }[];
  n_samples_requested: number;
  n_annotations_evaluated: number;
}

export interface Dataset {
  id: string;
  project_id: string;
  name: string;
  slug: string;
  created_at: string;
  image_count: number;
  ready_count: number;
}

export type ImageStatus = "uploaded" | "embedding" | "ready" | "failed";

export interface ImageRow {
  id: string;
  dataset_id: string;
  filename: string;
  width: number;
  height: number;
  bytes: number;
  status: ImageStatus;
  error: string | null;
  thumbnail_url: string | null;
  image_url: string;
  uncertainty: number | null;
  annotation_count: number;
  review_status: ReviewStatus;
  submitted_at: string | null;
  reviewed_at: string | null;
  review_reason: string | null;
  comment_count: number;
  open_comment_count: number;
}

export type GeometryType = "polygon" | "bbox" | "brush" | "polyline" | "keypoints" | "obb";

export interface KeypointSchema {
  keypoints: { name: string; color?: string }[];
  edges: [number, number][];
}

export type ClassPropertyType = "single" | "multi" | "text";

export interface ClassProperty {
  id: string;
  class_id: string;
  name: string;
  type: ClassPropertyType;
  options: string[];
  required: boolean;
  sort_order: number;
}

export interface ClassRow {
  id: string;
  project_id: string;
  name: string;
  color: string;
  geometry_type: GeometryType;
  description: string;
  annotation_count: number;
  parent_id: string | null;
  keypoint_schema: KeypointSchema | null;
}

export type AnnotationSource = "manual" | "model" | "imported";

export type ReviewStatus = "new" | "in_progress" | "submitted" | "approved" | "rejected";

export interface CommentRow {
  id: string;
  image_id: string;
  author_id: string;
  author_name: string;
  body: string;
  x: number | null;
  y: number | null;
  resolved: boolean;
  created_at: string;
}

export interface Annotation {
  id: string;
  image_id: string;
  class_id: string;
  geometry_type: GeometryType;
  geometry: {
    points?: number[][];        // polygon/polyline: [[x,y],...]; keypoints: [[x,y,v],...]
    x?: number; y?: number; w?: number; h?: number;  // bbox
    cx?: number; cy?: number; angle_rad?: number;     // obb (w/h reused)
  };
  source: AnnotationSource;
  confidence: number | null;
  properties: Record<string, string | string[]>;
  created_at: string;
}

export interface PredictResponse {
  polygon: [number, number][];
  score: number;
  bbox: [number, number, number, number];
}
