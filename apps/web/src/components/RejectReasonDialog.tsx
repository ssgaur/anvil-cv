import { useEffect, useRef, useState } from "react";

type RejectReasonDialogProps = {
  open: boolean;
  title?: string;
  description?: string;
  confirmLabel?: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
};

export function RejectReasonDialog({
  open,
  title = "Reject image",
  description = "Add a clear reason so the labeler knows what to fix.",
  confirmLabel = "Reject",
  busy = false,
  onCancel,
  onConfirm,
}: RejectReasonDialogProps) {
  const [reason, setReason] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) {
      setReason("");
      return;
    }
    const t = window.setTimeout(() => textareaRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open]);

  if (!open) return null;

  const trimmed = reason.trim();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="reject-reason-title"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <form
        className="w-full max-w-lg rounded-2xl border border-neutral-800 bg-neutral-950 shadow-2xl"
        onSubmit={(e) => {
          e.preventDefault();
          if (trimmed && !busy) onConfirm(trimmed);
        }}
      >
        <div className="border-b border-neutral-800 px-5 py-4">
          <h2 id="reject-reason-title" className="text-base font-semibold text-neutral-100">{title}</h2>
          <p className="mt-1 text-sm text-neutral-400">{description}</p>
        </div>
        <div className="px-5 py-4">
          <label className="text-xs font-medium uppercase tracking-wide text-neutral-500" htmlFor="reject-reason">
            Rejection reason
          </label>
          <textarea
            ref={textareaRef}
            id="reject-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && !busy) onCancel();
            }}
            rows={4}
            className="mt-2 w-full resize-y rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-rose-400 focus:ring-2 focus:ring-rose-500/30"
            placeholder="Example: Object boundary misses the leaf tip; please redraw and resubmit."
            disabled={busy}
          />
          {!trimmed && (
            <div className="mt-2 text-xs text-neutral-500">A reason is required before rejecting.</div>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-neutral-800 px-5 py-4">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-lg border border-neutral-700 px-3 py-1.5 text-sm text-neutral-200 hover:bg-neutral-800 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!trimmed || busy}
            className="rounded-lg bg-rose-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-400 disabled:bg-neutral-700 disabled:text-neutral-400"
          >
            {busy ? "Rejecting…" : confirmLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
