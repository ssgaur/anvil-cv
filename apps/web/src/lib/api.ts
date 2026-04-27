import type {
  AdminUser, Annotation, ClassProperty, ClassPropertyType, ClassRow, CommentRow, CompareResult, Dataset, ImageRow, ModelVersion, PredictResponse, Project, ReviewStatus, User, UserRole,
} from "../types";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

function token(): string | null {
  return localStorage.getItem("anvil_token");
}

function setToken(t: string | null) {
  if (t) localStorage.setItem("anvil_token", t);
  else localStorage.removeItem("anvil_token");
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(init.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json" }
      : {}),
    ...((init.headers as Record<string, string>) ?? {}),
  };
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  const r = await fetch(`${API}${path}`, { ...init, headers });
  if (r.status === 401) {
    setToken(null);
    if (location.pathname !== "/login") location.href = "/login";
    throw new Error("unauthorized");
  }
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = (j.detail && (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail))) || msg;
    } catch { /* noop */ }
    throw new Error(msg);
  }
  if (r.status === 204) return undefined as T;
  return (await r.json()) as T;
}

// ---- Helpers
export function fileUrl(rel: string | null | undefined): string {
  if (!rel) return "";
  if (rel.startsWith("http")) return rel;
  return `${API}${rel}`;
}

export async function downloadCocoExport(
  projectId: string, datasetId: string, filename: string,
  opts?: { review_status?: string },
) {
  const t = token();
  const qs = opts?.review_status ? `?review_status=${encodeURIComponent(opts.review_status)}` : "";
  const r = await fetch(`${API}/api/projects/${projectId}/datasets/${datasetId}/export/coco${qs}`, {
    headers: t ? { Authorization: `Bearer ${t}` } : undefined,
  });
  if (!r.ok) throw new Error(`export failed: ${r.status} ${r.statusText}`);
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const api = {
  setToken,
  token,
  health: () => req<{ status: string; version: string }>("/health"),

  // Auth
  login: async (email: string, password: string) => {
    const r = await req<{ access_token: string }>("/api/auth/login", {
      method: "POST", body: JSON.stringify({ email, password }),
    });
    setToken(r.access_token);
    return r;
  },
  signup: async (email: string, name: string, password: string) => {
    const r = await req<{ access_token: string }>("/api/auth/signup", {
      method: "POST", body: JSON.stringify({ email, name, password }),
    });
    setToken(r.access_token);
    return r;
  },
  me: () => req<User>("/api/auth/me"),
  logout: () => { setToken(null); location.href = "/login"; },

  // Auth providers (which login methods are enabled on this deployment)
  providers: () =>
    req<{ password: boolean; signup: boolean; oauth: Record<string, boolean> }>(
      "/api/auth/providers",
    ),
  oauthLoginUrl: (provider: "google" | "github") =>
    `${API}/api/auth/oauth/${provider}/login`,

  // Projects
  listProjects: () => req<Project[]>("/api/projects"),
  createProject: (body: { name: string; description?: string }) =>
    req<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  getProject: (id: string) => req<Project>(`/api/projects/${id}`),
  deleteProject: (id: string) =>
    req<void>(`/api/projects/${id}`, { method: "DELETE" }),

  // Datasets
  listDatasets: (projectId: string) =>
    req<Dataset[]>(`/api/projects/${projectId}/datasets`),
  createDataset: (projectId: string, body: { name: string }) =>
    req<Dataset>(`/api/projects/${projectId}/datasets`, {
      method: "POST", body: JSON.stringify(body),
    }),
  getDataset: (projectId: string, datasetId: string) =>
    req<Dataset>(`/api/projects/${projectId}/datasets/${datasetId}`),
  deleteDataset: (projectId: string, datasetId: string) =>
    req<void>(`/api/projects/${projectId}/datasets/${datasetId}`, { method: "DELETE" }),

  // Images
  listImages: (
    projectId: string, datasetId: string,
    opts?: { status?: string; review_status?: ReviewStatus; limit?: number; offset?: number },
  ) => {
    const q = new URLSearchParams();
    if (opts?.status) q.set("status_filter", opts.status);
    if (opts?.review_status) q.set("review_status", opts.review_status);
    if (opts?.limit) q.set("limit", String(opts.limit));
    if (opts?.offset) q.set("offset", String(opts.offset));
    const qs = q.toString();
    return req<ImageRow[]>(
      `/api/projects/${projectId}/datasets/${datasetId}/images${qs ? `?${qs}` : ""}`,
    );
  },
  uploadImages: (projectId: string, datasetId: string, files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return req<ImageRow[]>(
      `/api/projects/${projectId}/datasets/${datasetId}/images`,
      { method: "POST", body: fd },
    );
  },
  getImage: (id: string) => req<ImageRow>(`/api/images/${id}`),

  // Review flow
  reviewAction: (id: string, body: { action: "submit" | "approve" | "reject" | "reopen"; reason?: string }) =>
    req<ImageRow>(`/api/images/${id}/review`, {
      method: "PATCH", body: JSON.stringify(body),
    }),

  // Comments (spatial pins + general)
  listComments: (imageId: string) =>
    req<CommentRow[]>(`/api/images/${imageId}/comments`),
  createComment: (imageId: string, body: { body: string; x?: number | null; y?: number | null }) =>
    req<CommentRow>(`/api/images/${imageId}/comments`, {
      method: "POST", body: JSON.stringify(body),
    }),
  patchComment: (id: string, body: { body?: string; resolved?: boolean }) =>
    req<CommentRow>(`/api/comments/${id}`, {
      method: "PATCH", body: JSON.stringify(body),
    }),
  deleteComment: (id: string) =>
    req<void>(`/api/comments/${id}`, { method: "DELETE" }),

  importCocoZip: (projectId: string, datasetId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<{
      new_classes: number;
      matched_existing_classes: number;
      images_imported: number;
      images_skipped_missing_or_invalid: number;
      polygons_imported: number;
      bboxes_imported: number;
      rle_segmentations_skipped: number;
      annotations_skipped_no_image: number;
      annotations_skipped_no_class: number;
    }>(
      `/api/projects/${projectId}/datasets/${datasetId}/import/coco`,
      { method: "POST", body: fd },
    );
  },

  // Classes
  listClasses: (projectId: string) =>
    req<ClassRow[]>(`/api/projects/${projectId}/classes`),
  createClass: (projectId: string, body: { name: string; color?: string; geometry_type?: string; description?: string }) =>
    req<ClassRow>(`/api/projects/${projectId}/classes`, {
      method: "POST", body: JSON.stringify(body),
    }),
  patchClass: (projectId: string, classId: string, body: {
    name?: string; color?: string; geometry_type?: string; description?: string;
  }) =>
    req<ClassRow>(`/api/projects/${projectId}/classes/${classId}`, {
      method: "PATCH", body: JSON.stringify(body),
    }),
  deleteClass: (projectId: string, classId: string) =>
    req<void>(`/api/projects/${projectId}/classes/${classId}`, { method: "DELETE" }),

  // Class properties (per-class attribute schema)
  listClassProperties: (projectId: string, classId: string) =>
    req<ClassProperty[]>(`/api/projects/${projectId}/classes/${classId}/properties`),
  createClassProperty: (projectId: string, classId: string, body: {
    name: string; type: ClassPropertyType; options?: string[]; required?: boolean; sort_order?: number;
  }) =>
    req<ClassProperty>(`/api/projects/${projectId}/classes/${classId}/properties`, {
      method: "POST", body: JSON.stringify(body),
    }),
  patchClassProperty: (projectId: string, classId: string, propertyId: string, body: {
    name?: string; type?: ClassPropertyType; options?: string[]; required?: boolean; sort_order?: number;
  }) =>
    req<ClassProperty>(`/api/projects/${projectId}/classes/${classId}/properties/${propertyId}`, {
      method: "PATCH", body: JSON.stringify(body),
    }),
  deleteClassProperty: (projectId: string, classId: string, propertyId: string) =>
    req<void>(`/api/projects/${projectId}/classes/${classId}/properties/${propertyId}`, { method: "DELETE" }),

  // Annotations
  listAnnotations: (imageId: string) =>
    req<Annotation[]>(`/api/images/${imageId}/annotations`),
  createAnnotation: (imageId: string, body: {
    class_id: string;
    geometry_type: string;
    geometry: object;
    source?: string;
    confidence?: number | null;
  }) =>
    req<Annotation>(`/api/images/${imageId}/annotations`, {
      method: "POST", body: JSON.stringify(body),
    }),
  deleteAnnotation: (id: string) =>
    req<void>(`/api/annotations/${id}`, { method: "DELETE" }),
  updateAnnotation: (id: string, patch: { geometry?: any; geometry_type?: string; class_id?: string; properties?: Record<string, string | string[]> }) =>
    req<Annotation>(`/api/annotations/${id}`, {
      method: "PATCH", body: JSON.stringify(patch),
    }),

  // Predict
  predict: (imageId: string, body: {
    points?: [number, number][]; point_labels?: number[]; box?: [number, number, number, number];
    model_version_id?: string | null;
  }) =>
    req<PredictResponse>(`/api/images/${imageId}/predict`, {
      method: "POST", body: JSON.stringify(body),
    }),

  // Model versions (fine-tuned LoRA adapters)
  listModelVersions: (projectId: string) =>
    req<ModelVersion[]>(`/api/projects/${projectId}/model-versions`),
  getModelVersion: (id: string) =>
    req<ModelVersion>(`/api/model-versions/${id}`),
  createModelVersion: (projectId: string, body: {
    name: string; dataset_id: string;
    epochs?: number; lr?: number; rank?: number; alpha?: number; eval_split?: number;
  }) =>
    req<ModelVersion>(`/api/projects/${projectId}/model-versions`, {
      method: "POST", body: JSON.stringify(body),
    }),
  setActiveModel: (projectId: string, modelVersionId: string | null) =>
    req<ModelVersion | null>(`/api/projects/${projectId}/active-model`, {
      method: "POST", body: JSON.stringify({ model_version_id: modelVersionId }),
    }),
  deleteModelVersion: (id: string) =>
    req<void>(`/api/model-versions/${id}`, { method: "DELETE" }),
  compareModelVersions: (projectId: string, body: {
    a_id: string | null; b_id: string | null;
    dataset_id?: string | null; n_samples?: number; seed?: number | null;
  }) =>
    req<CompareResult>(`/api/projects/${projectId}/model-versions/compare`, {
      method: "POST", body: JSON.stringify(body),
    }),

  // Active learning
  nextUncertain: (projectId: string, datasetId: string) =>
    req<{ scored_now: number; image: ImageRow | null }>(
      `/api/projects/${projectId}/datasets/${datasetId}/next-uncertain`,
      { method: "POST" },
    ),

  // Admin: user management
  adminListUsers: () => req<AdminUser[]>(`/api/admin/users`),
  adminCreateUser: (body: { email: string; name: string; role: UserRole; password?: string }) =>
    req<AdminUser>(`/api/admin/users`, { method: "POST", body: JSON.stringify(body) }),
  adminGetUser: (id: string) => req<AdminUser>(`/api/admin/users/${id}`),
  adminPatchUser: (id: string, body: { name?: string; role?: UserRole; password?: string }) =>
    req<AdminUser>(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  adminDeleteUser: (id: string) =>
    req<void>(`/api/admin/users/${id}`, { method: "DELETE" }),

  // First-run setup wizard
  setupStatus: () => req<{ needs_setup: boolean }>(`/api/setup/status`),
  setupInitialize: async (body: { email: string; name: string; password: string }) => {
    const r = await req<{ access_token: string }>(
      `/api/setup/initialize`, { method: "POST", body: JSON.stringify(body) },
    );
    setToken(r.access_token);
    return r;
  },
};
