import type {
  Annotation, ClassRow, Dataset, ImageRow, ModelVersion, PredictResponse, Project, User,
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
    opts?: { status?: string; limit?: number; offset?: number },
  ) => {
    const q = new URLSearchParams();
    if (opts?.status) q.set("status_filter", opts.status);
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

  // Classes
  listClasses: (projectId: string) =>
    req<ClassRow[]>(`/api/projects/${projectId}/classes`),
  createClass: (projectId: string, body: { name: string; color?: string; geometry_type?: string }) =>
    req<ClassRow>(`/api/projects/${projectId}/classes`, {
      method: "POST", body: JSON.stringify(body),
    }),
  deleteClass: (projectId: string, classId: string) =>
    req<void>(`/api/projects/${projectId}/classes/${classId}`, { method: "DELETE" }),

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

  // Active learning
  nextUncertain: (projectId: string, datasetId: string) =>
    req<{ scored_now: number; image: ImageRow | null }>(
      `/api/projects/${projectId}/datasets/${datasetId}/next-uncertain`,
    ),
};
