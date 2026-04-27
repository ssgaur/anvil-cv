import { useToasts } from "../lib/toast";

const STYLES: Record<string, string> = {
  info:    "bg-neutral-800 border-neutral-700 text-neutral-100",
  success: "bg-emerald-900/80 border-emerald-700 text-emerald-100",
  warning: "bg-amber-900/80 border-amber-700 text-amber-100",
  error:   "bg-rose-900/80 border-rose-700 text-rose-100",
};

const ICONS: Record<string, string> = {
  info: "ℹ", success: "✓", warning: "⚠", error: "✕",
};

export function Toaster() {
  const items = useToasts((s) => s.items);
  const dismiss = useToasts((s) => s.dismiss);

  if (items.length === 0) return null;
  return (
    <div className="fixed top-4 right-4 z-[1000] space-y-2 w-[320px] max-w-[90vw] pointer-events-none">
      {items.map((t) => (
        <div
          key={t.id}
          role="alert"
          className={`pointer-events-auto border rounded-md shadow-lg px-3 py-2 text-sm flex items-start gap-2 ${STYLES[t.variant]}`}
        >
          <span aria-hidden className="font-bold mt-0.5">{ICONS[t.variant]}</span>
          <div className="flex-1 min-w-0">
            <div className="font-medium break-words">{t.title}</div>
            {t.body && <div className="text-xs opacity-80 mt-0.5 break-words">{t.body}</div>}
          </div>
          <button
            aria-label="dismiss"
            onClick={() => dismiss(t.id)}
            className="text-xs opacity-60 hover:opacity-100"
          >✕</button>
        </div>
      ))}
    </div>
  );
}
