// Domain types mirroring API schemas

export interface User {
  id: string;
  email: string;
  name: string;
  created_at: string;
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
}

export type GeometryType = "polygon" | "bbox" | "brush";

export interface ClassRow {
  id: string;
  project_id: string;
  name: string;
  color: string;
  geometry_type: GeometryType;
  description: string;
  annotation_count: number;
}

export type AnnotationSource = "manual" | "model" | "imported";

export interface Annotation {
  id: string;
  image_id: string;
  class_id: string;
  geometry_type: GeometryType;
  geometry: { points?: [number, number][]; x?: number; y?: number; w?: number; h?: number };
  source: AnnotationSource;
  confidence: number | null;
  created_at: string;
}

export interface PredictResponse {
  polygon: [number, number][];
  score: number;
  bbox: [number, number, number, number];
}
