import type {
  AdminUser, Annotation, ClassProperty, ClassPropertyType, ClassRow, CommentRow,
  CompareResult, Dataset, ImageRow, KeypointSchema, ModelVersion, PredictResponse,
  Project, ReviewStatus, ShowcaseDiscovery, ShowcaseRemoval,
  ShowcaseRuntime, ShowcaseVideo, User, UserRole,
} from "../types";

export type ImageSort =
  | "recent" | "oldest" | "filename" | "most_labels" | "fewest_labels" | "uncertainty";

export interface ImageListOpts {
  status?: string;
  review_status?: ReviewStatus;
  has_annotations?: boolean;
  class_id?: string[];
  sort?: ImageSort;
  limit?: number;
  offset?: number;
}

function buildImageQuery(opts?: ImageListOpts): string {
  const q = new URLSearchParams();
  if (opts?.status) q.set("status_filter", opts.status);
  if (opts?.review_status) q.set("review_status", opts.review_status);
  if (opts?.has_annotations !== undefined) q.set("has_annotations", String(opts.has_annotations));
  if (opts?.class_id) opts.class_id.forEach((c) => q.append("class_id", c));
  if (opts?.sort) q.set("sort", opts.sort);
  if (opts?.limit) q.set("limit", String(opts.limit));
  if (opts?.offset) q.set("offset", String(opts.offset));
  return q.toString();
}

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/* Token transport: HttpOnly cookies set by the server.
 * The client used to keep an access token in localStorage; that storage is
 * now actively cleared on first load to migrate existing sessions. */
function token(): string | null {
  // Kept for back-compat (older code paths checking for a session before
  // calling /me); always null now that auth lives in cookies.
  return null;
}

function setToken(_t: string | null) {
  // No-op; cookies are managed by the server. Always wipe the legacy entry.
  try { localStorage.removeItem("anvil_token"); } catch { /* noop */ }
}

// Wipe legacy storage once at module load so stale tokens can't shadow cookies.
try { localStorage.removeItem("anvil_token"); } catch { /* noop */ }

const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// Single-flight refresh dedupe: when many in-flight calls 401 at once, we
// only want one /api/auth/refresh round-trip; everyone else awaits its
// outcome and retries their original request once.
let refreshInFlight: Promise<boolean> | null = null;
async function attemptRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const r = await fetch(`${API}/api/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: { "X-Anvil-CSRF": "1" },
        });
        return r.ok;
      } catch {
        return false;
      } finally {
        // Clear next tick so subsequent unrelated 401s can retry independently.
        setTimeout(() => { refreshInFlight = null; }, 0);
      }
    })();
  }
  return refreshInFlight;
}

async function rawFetch(path: string, init: RequestInit, headers: Record<string, string>) {
  return fetch(`${API}${path}`, { ...init, headers, credentials: "include" });
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(init.body && !(init.body instanceof FormData)
      ? { "Content-Type": "application/json" }
      : {}),
    ...((init.headers as Record<string, string>) ?? {}),
  };
  const method = (init.method ?? "GET").toUpperCase();
  if (UNSAFE.has(method)) headers["X-Anvil-CSRF"] = "1";

  let r = await rawFetch(path, init, headers);
  // Try one silent refresh on 401 for non-auth endpoints; if refresh works,
  // replay the original request once. Auth endpoints handle their own flow.
  if (r.status === 401 && !path.startsWith("/api/auth/")) {
    const ok = await attemptRefresh();
    if (ok) r = await rawFetch(path, init, headers);
  }
  if (r.status === 401) {
    if (location.pathname !== "/login" && !path.startsWith("/api/auth/")) {
      location.href = "/login";
    }
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
  const qs = opts?.review_status ? `?review_status=${encodeURIComponent(opts.review_status)}` : "";
  const r = await fetch(`${API}/api/projects/${projectId}/datasets/${datasetId}/export/coco${qs}`, {
    credentials: "include",
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

  // Local SAM3 + personal-use video editor
  showcaseRuntime: () => req<ShowcaseRuntime>("/api/showcase/runtime"),
  listShowcaseVideos: () => req<ShowcaseVideo[]>("/api/showcase/videos"),
  getShowcaseVideo: (id: string) =>
    req<ShowcaseVideo>(`/api/showcase/videos/${id}`),
  uploadShowcaseVideo: (file: File) =>
    req<ShowcaseVideo>("/api/showcase/videos", {
      method: "POST",
      body: file,
      headers: {
        "Content-Type": file.type || "application/octet-stream",
        "X-Filename": encodeURIComponent(file.name),
      },
    }),
  deleteShowcaseVideo: (id: string) =>
    req<void>(`/api/showcase/videos/${id}`, { method: "DELETE" }),
  discoverShowcaseObjects: (
    id: string,
    body: { timestamp_s: number; concept: string; threshold?: number },
  ) =>
    req<ShowcaseDiscovery>(`/api/showcase/videos/${id}/discover`, {
      method: "POST", body: JSON.stringify(body),
    }),
  createShowcaseRemoval: (
    id: string,
    body: {
      request_id: string;
      concept: string;
      start_s: number;
      end_s: number;
      seed_s: number;
      selected_scores: number[];
      mask_rles: { size: [number, number]; counts: string }[];
      manual_mask_png?: string | null;
    },
  ) =>
    req<ShowcaseRemoval>(`/api/showcase/videos/${id}/removals`, {
      method: "POST", body: JSON.stringify(body),
    }),
  getShowcaseRemoval: (id: string) =>
    req<ShowcaseRemoval>(`/api/showcase/removals/${id}`),
  pauseShowcaseRemoval: (id: string) =>
    req<ShowcaseRemoval>(`/api/showcase/removals/${id}/pause`, { method: "POST" }),
  resumeShowcaseRemoval: (id: string) =>
    req<ShowcaseRemoval>(`/api/showcase/removals/${id}/resume`, { method: "POST" }),
  cancelShowcaseRemoval: (id: string) =>
    req<ShowcaseRemoval>(`/api/showcase/removals/${id}`, { method: "DELETE" }),
  listShowcaseRemovals: (videoId: string) =>
    req<ShowcaseRemoval[]>(`/api/showcase/videos/${videoId}/removals`),

  // Auth
  login: async (email: string, password: string) => {
    return await req<{ access_token: string }>("/api/auth/login", {
      method: "POST", body: JSON.stringify({ email, password }),
    });
  },
  signup: async (email: string, name: string, password: string) => {
    return await req<{ access_token: string }>("/api/auth/signup", {
      method: "POST", body: JSON.stringify({ email, name, password }),
    });
  },
  me: () => req<User>("/api/auth/me"),
  logout: async () => {
    try { await req<void>("/api/auth/logout", { method: "POST" }); } catch { /* noop */ }
    setToken(null);
    location.href = "/login";
  },

  // Password reset
  passwordResetRequest: (email: string) =>
    req<void>("/api/auth/password-reset/request", {
      method: "POST", body: JSON.stringify({ email }),
    }),
  passwordResetConfirm: (token: string, password: string) =>
    req<void>("/api/auth/password-reset/confirm", {
      method: "POST", body: JSON.stringify({ token, password }),
    }),

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
    opts?: ImageListOpts,
  ) => {
    const qs = buildImageQuery(opts);
    return req<ImageRow[]>(
      `/api/projects/${projectId}/datasets/${datasetId}/images${qs ? `?${qs}` : ""}`,
    );
  },
  /** Paginated variant returning {items,total} (uses X-Total-Count header). */
  listImagesPaged: async (
    projectId: string, datasetId: string,
    opts?: ImageListOpts,
  ): Promise<{ items: ImageRow[]; total: number }> => {
    const qs = buildImageQuery(opts);
    const r = await fetch(
      `${API}/api/projects/${projectId}/datasets/${datasetId}/images${qs ? `?${qs}` : ""}`,
      { credentials: "include" },
    );
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    const items = (await r.json()) as ImageRow[];
    const total = parseInt(r.headers.get("X-Total-Count") ?? String(items.length), 10);
    return { items, total: Number.isFinite(total) ? total : items.length };
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
  createClass: (projectId: string, body: { name: string; color?: string; geometry_type?: string; description?: string; parent_id?: string | null; keypoint_schema?: KeypointSchema | null }) =>
    req<ClassRow>(`/api/projects/${projectId}/classes`, {
      method: "POST", body: JSON.stringify(body),
    }),
  patchClass: (projectId: string, classId: string, body: {
    name?: string; color?: string; geometry_type?: string; description?: string;
    parent_id?: string | null; set_parent_root?: boolean;
    keypoint_schema?: KeypointSchema | null;
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

  // Bulk ops on images / annotations
  bulkDeleteImages: (projectId: string, datasetId: string, imageIds: string[]) =>
    req<{ ok: string[]; failed: { id: string; reason: string }[] }>(
      `/api/projects/${projectId}/datasets/${datasetId}/images/bulk-delete`,
      { method: "POST", body: JSON.stringify({ image_ids: imageIds }) },
    ),
  bulkReviewImages: (
    projectId: string, datasetId: string,
    body: { image_ids: string[]; action: "reset" | "reopen" | "reject"; reason?: string },
  ) =>
    req<{ ok: string[]; failed: { id: string; reason: string }[] }>(
      `/api/projects/${projectId}/datasets/${datasetId}/images/bulk-review`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  bulkMoveImages: (
    projectId: string, datasetId: string,
    body: { image_ids: string[]; target_dataset_id: string },
  ) =>
    req<{ ok: string[]; failed: { id: string; reason: string }[] }>(
      `/api/projects/${projectId}/datasets/${datasetId}/images/bulk-move`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  bulkDeleteAnnotationsByClass: (
    projectId: string, datasetId: string,
    body: { class_id: string; image_ids?: string[]; include_locked?: boolean },
  ) =>
    req<{ ok: string[]; failed: { id: string; reason: string }[] }>(
      `/api/projects/${projectId}/datasets/${datasetId}/annotations/bulk-delete-by-class`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  // First-run setup wizard
  setupStatus: () => req<{ needs_setup: boolean }>(`/api/setup/status`),
  setupInitialize: async (body: { email: string; name: string; password: string }) => {
    return await req<{ access_token: string }>(
      `/api/setup/initialize`, { method: "POST", body: JSON.stringify(body) },
    );
  },
};
