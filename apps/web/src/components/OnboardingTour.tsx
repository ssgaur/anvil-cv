import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";

const KEY = "anvil:onboarding:v1";

const STEPS = [
  {
    title: "1. Create a project",
    body: "Projects contain datasets, classes, model versions, and review settings. Start from the Projects page.",
    href: "/projects",
    cta: "Go to projects",
  },
  {
    title: "2. Add classes",
    body: "Open a project, then use the Classes tab to define polygon, bbox, polyline, OBB, or keypoint classes.",
    href: "/projects",
    cta: "Open a project",
  },
  {
    title: "3. Upload images",
    body: "Open a dataset and upload images. The worker pre-computes SAM2 embeddings so click-to-segment is fast.",
    href: "/projects",
    cta: "Find a dataset",
  },
  {
    title: "4. Label, review, fine-tune",
    body: "In Workview, press ? for shortcuts. Use A for SAM2, then export COCO or fine-tune a SAM2 LoRA model.",
    href: "/projects",
    cta: "Start labeling",
  },
] as const;

export function OnboardingTour() {
  const loc = useLocation();
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(KEY) === "dismissed"; }
    catch { return true; }
  });
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (loc.pathname === "/login" || loc.pathname === "/setup") {
      setDismissed(true);
    }
  }, [loc.pathname]);

  const current = useMemo(() => STEPS[Math.min(step, STEPS.length - 1)], [step]);
  if (dismissed) return null;

  function dismiss() {
    try { localStorage.setItem(KEY, "dismissed"); } catch { /* noop */ }
    setDismissed(true);
  }

  return (
    <div className="fixed right-5 bottom-5 z-50 w-[360px] max-w-[calc(100vw-2rem)] rounded-xl border border-emerald-500/40 bg-neutral-950 shadow-2xl">
      <div className="p-4 border-b border-neutral-800">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-lg bg-emerald-500 text-neutral-950 grid place-items-center font-bold shrink-0">
            {step + 1}
          </div>
          <div>
            <div className="text-sm font-semibold text-neutral-100">{current.title}</div>
            <div className="text-xs text-neutral-400 mt-1 leading-relaxed">{current.body}</div>
          </div>
          <button onClick={dismiss} className="ml-auto text-neutral-500 hover:text-neutral-200" title="Dismiss tour">
            x
          </button>
        </div>
      </div>
      <div className="p-3 flex items-center gap-2">
        <div className="flex gap-1 mr-auto">
          {STEPS.map((_, i) => (
            <span
              key={i}
              className={`w-1.5 h-1.5 rounded-full ${i === step ? "bg-emerald-400" : "bg-neutral-700"}`}
            />
          ))}
        </div>
        <Link to={current.href} className="text-xs text-neutral-300 hover:text-white px-2 py-1">
          {current.cta}
        </Link>
        {step > 0 && (
          <button onClick={() => setStep((s) => Math.max(0, s - 1))} className="text-xs text-neutral-400 hover:text-white px-2 py-1">
            Back
          </button>
        )}
        {step < STEPS.length - 1 ? (
          <button onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))} className="text-xs bg-emerald-500 hover:bg-emerald-400 text-neutral-950 font-medium px-3 py-1.5 rounded">
            Next
          </button>
        ) : (
          <button onClick={dismiss} className="text-xs bg-emerald-500 hover:bg-emerald-400 text-neutral-950 font-medium px-3 py-1.5 rounded">
            Done
          </button>
        )}
      </div>
    </div>
  );
}

