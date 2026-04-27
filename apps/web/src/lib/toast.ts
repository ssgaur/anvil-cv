import { create } from "zustand";

export type ToastVariant = "info" | "success" | "error" | "warning";

export interface Toast {
  id: number;
  variant: ToastVariant;
  title: string;
  body?: string;
  // ms; 0 means sticky until dismissed
  durationMs: number;
}

interface ToastState {
  items: Toast[];
  push: (t: Omit<Toast, "id">) => number;
  dismiss: (id: number) => void;
  clear: () => void;
}

let nextId = 1;

export const useToasts = create<ToastState>((set, get) => ({
  items: [],
  push: (t) => {
    const id = nextId++;
    const toast: Toast = { id, ...t };
    set((s) => ({ items: [...s.items, toast] }));
    if (toast.durationMs > 0) {
      setTimeout(() => get().dismiss(id), toast.durationMs);
    }
    return id;
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((t) => t.id !== id) })),
  clear: () => set({ items: [] }),
}));

/** Parse our backend's error shape — single string OR JSON-stringified
 *  detail object {detail, retry_after} — into a friendly title. */
export function describeError(err: unknown): string {
  if (err instanceof Error) {
    try {
      const obj = JSON.parse(err.message);
      // detail object: {detail: "...", retry_after: N}
      if (obj && typeof obj === "object" && "detail" in obj) {
        const ra = (obj as { retry_after?: number }).retry_after;
        const text = String((obj as { detail: unknown }).detail);
        return ra ? `${text} (retry in ${ra}s)` : text;
      }
    } catch { /* not JSON */ }
    return err.message;
  }
  return String(err);
}

export const toast = {
  info: (title: string, body?: string) => useToasts.getState().push({
    variant: "info", title, body, durationMs: 4000,
  }),
  success: (title: string, body?: string) => useToasts.getState().push({
    variant: "success", title, body, durationMs: 3500,
  }),
  warn: (title: string, body?: string) => useToasts.getState().push({
    variant: "warning", title, body, durationMs: 5000,
  }),
  error: (errOrTitle: string | unknown, title?: string) => {
    // If a title is provided, treat first arg as the underlying error/cause.
    const isErr = errOrTitle instanceof Error || (typeof errOrTitle === "object" && errOrTitle !== null);
    const finalTitle = title ?? (typeof errOrTitle === "string" ? errOrTitle : describeError(errOrTitle));
    const finalBody = title && isErr ? describeError(errOrTitle) : undefined;
    return useToasts.getState().push({
      variant: "error", title: finalTitle, body: finalBody, durationMs: 6000,
    });
  },
};
