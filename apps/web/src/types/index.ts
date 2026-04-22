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
