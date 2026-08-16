import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api, fileUrl } from "../lib/api";
import type {
  ShowcaseDiscovery,
  ShowcaseRemoval,
  ShowcaseRuntime,
  ShowcaseVideo,
} from "../types";

const CONCEPTS = ["person", "child", "flag", "chair", "bag"];

function clock(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function megabytes(value: number) {
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function duration(seconds: number) {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  return [
    hours ? `${hours}h` : "",
    hours || minutes ? `${minutes}m` : "",
    `${rest}s`,
  ].filter(Boolean).join(" ");
}

export function ModelShowcasePage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const [runtime, setRuntime] = useState<ShowcaseRuntime | null>(null);
  const [videos, setVideos] = useState<ShowcaseVideo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [concept, setConcept] = useState("person");
  const [discovery, setDiscovery] = useState<ShowcaseDiscovery | null>(null);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [clipStart, setClipStart] = useState(0);
  const [clipEnd, setClipEnd] = useState(5);
  const [fullVideo, setFullVideo] = useState(true);
  const [discovering, setDiscovering] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [removal, setRemoval] = useState<ShowcaseRemoval | null>(null);
  const [submittingRemoval, setSubmittingRemoval] = useState(false);
  const submitLockRef = useRef(false);
  const [error, setError] = useState<string | null>(null);

  const active = videos.find((video) => video.id === activeId) ?? videos[0] ?? null;
  const selected = discovery
    ? selectedIndices
        .map((index) => discovery.instances[index])
        .filter((item) => item != null)
    : [];

  async function load() {
    const [nextRuntime, nextVideos] = await Promise.all([
      api.showcaseRuntime(),
      api.listShowcaseVideos(),
    ]);
    setRuntime(nextRuntime);
    setVideos((current) =>
      nextVideos.map((next) => {
        const previous = current.find((item) => item.id === next.id);
        return previous?.status === "ready" && next.status === "ready"
          ? { ...next, playback_url: previous.playback_url, poster_url: previous.poster_url }
          : next;
      }),
    );
    setActiveId((current) => current ?? nextVideos[0]?.id ?? null);
  }

  useEffect(() => {
    load().catch((reason) =>
      setError(reason instanceof Error ? reason.message : String(reason)),
    );
  }, []);

  useEffect(() => {
    if (!videos.some((video) => video.status === "preparing")) return;
    const timer = window.setInterval(() => load().catch(() => undefined), 2500);
    return () => window.clearInterval(timer);
  }, [videos]);

  useEffect(() => {
    setDiscovery(null);
    setSelectedIndices([]);
    setRemoval(null);
    setError(null);
    if (activeId) {
      api.listShowcaseRemovals(activeId)
        .then((jobs) =>
          setRemoval(
            jobs.find((job) => ["queued", "running"].includes(job.status)) ??
              jobs.find((job) => job.status === "completed") ??
              null,
          ),
        )
        .catch(() => undefined);
    }
  }, [activeId]);

  useEffect(() => {
    if (!removal || ["completed", "failed", "cancelled"].includes(removal.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const updated = await api.getShowcaseRemoval(removal.id);
        setRemoval(updated.status === "cancelled" ? null : updated);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [removal]);

  useEffect(() => {
    if (!fullVideo || active?.duration_s == null) return;
    setClipStart(0);
    setClipEnd(active.duration_s);
  }, [active?.id, active?.duration_s, fullVideo]);

  async function upload(file: File) {
    setUploading(true);
    setError(null);
    try {
      const created = await api.uploadShowcaseVideo(file);
      setVideos((current) => [created, ...current]);
      setActiveId(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setUploading(false);
    }
  }

  async function highlight() {
    if (!active || active.status !== "ready" || discovering) return;
    const timestamp = videoRef.current?.currentTime ?? 0;
    videoRef.current?.pause();
    setDiscovering(true);
    setDiscovery(null);
    setSelectedIndices([]);
    setRemoval(null);
    setError(null);
    try {
      const result = await api.discoverShowcaseObjects(active.id, {
        timestamp_s: timestamp,
        concept: concept.trim(),
        threshold: 0.3,
      });
      setDiscovery(result);
      const duration = active.duration_s ?? result.timestamp_s + 5;
      if (fullVideo) {
        setClipStart(0);
        setClipEnd(Number(duration.toFixed(3)));
      } else {
        const start = Math.max(0, result.timestamp_s - 2.5);
        setClipStart(Number(start.toFixed(2)));
        setClipEnd(Number(Math.min(duration, start + 5).toFixed(2)));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDiscovering(false);
    }
  }

  async function removeSelected() {
    if (
      !active ||
      !discovery ||
      selected.length === 0 ||
      removal ||
      submitLockRef.current
    ) return;
    submitLockRef.current = true;
    setSubmittingRemoval(true);
    setError(null);
    try {
      setRemoval(
        await api.createShowcaseRemoval(active.id, {
          request_id: crypto.randomUUID(),
          concept: discovery.concept,
          start_s: clipStart,
          end_s: clipEnd,
          seed_s: discovery.timestamp_s,
          selected_scores: selected.map((item) => item.score),
          mask_rles: selected.map((item) => item.mask_rle),
        }),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      submitLockRef.current = false;
      setSubmittingRemoval(false);
    }
  }

  function selectObject(index: number) {
    setSelectedIndices((current) =>
      current.includes(index)
        ? current.filter((value) => value !== index)
        : [...current, index],
    );
    setRemoval(null);
  }

  const propainterReady = runtime?.personal_worker?.propainter ?? false;

  return (
    <div className="min-h-full bg-[#07090d] text-neutral-100">
      <header className="border-b border-white/10 bg-neutral-950/70 px-6 py-4">
        <div className="mx-auto flex max-w-[1700px] items-center justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-400">
              <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_16px_#34d399]" />
              Local video editor
            </div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">
              Remove objects from video
            </h1>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <RuntimePill ok={runtime?.sam3 ?? false} label="SAM3" detail="masks" />
            <RuntimePill
              ok={propainterReady}
              label="ProPainter"
              detail="personal use"
            />
            <span className="rounded-full border border-white/10 px-3 py-1.5 text-neutral-400">
              original preserved
            </span>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1700px] gap-4 p-4 xl:grid-cols-[minmax(0,1.55fr)_430px]">
        <section className="min-w-0 overflow-hidden rounded-2xl border border-white/10 bg-neutral-950 shadow-2xl">
          <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">
                {active?.filename ?? "No video loaded"}
              </div>
              {active?.status === "ready" && (
                <div className="mt-0.5 flex gap-3 text-[11px] text-neutral-500">
                  <span>{active.width}×{active.height}</span>
                  <span>{clock(active.duration_s)}</span>
                  <span>{active.fps?.toFixed(1)} fps</span>
                  <span>{megabytes(active.bytes)}</span>
                  <span className="text-emerald-500">source locked</span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {videos.length > 1 && (
                <select
                  value={active?.id ?? ""}
                  onChange={(event) => setActiveId(event.target.value)}
                  disabled={uploading || !!removal}
                  className="max-w-48 rounded-lg border border-white/10 bg-neutral-900 px-2 py-1.5 text-xs"
                >
                  {videos.map((video) => (
                    <option value={video.id} key={video.id}>{video.filename}</option>
                  ))}
                </select>
              )}
              <button
                onClick={() => uploadRef.current?.click()}
                disabled={uploading || !!removal}
                className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs hover:bg-white/10 disabled:opacity-50"
              >
                {uploading ? "Uploading…" : "Load video"}
              </button>
              <input
                ref={uploadRef}
                hidden
                type="file"
                accept="video/*,.mov"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void upload(file);
                  event.target.value = "";
                }}
              />
            </div>
          </div>

          <div className="relative aspect-video bg-black">
            {active?.status === "ready" && active.playback_url ? (
              <>
                <video
                  ref={videoRef}
                  className="h-full w-full object-contain"
                  controls
                  preload="metadata"
                  poster={fileUrl(active.poster_url)}
                  src={fileUrl(active.playback_url)}
                  onPlay={() => {
                    if (discovery) {
                      setDiscovery(null);
                      setSelectedIndices([]);
                    }
                  }}
                />
                {discovery && active?.width && active?.height && (
                  <div
                    className="pointer-events-none absolute left-1/2 top-1/2"
                    style={contentRect(active.width, active.height)}
                  >
                    <ObjectOverlay
                      discovery={discovery}
                      selectedIndices={selectedIndices}
                      onSelect={selectObject}
                    />
                  </div>
                )}
              </>
            ) : (
              <EmptyVideo
                active={active}
                uploading={uploading}
                onLoad={() => uploadRef.current?.click()}
              />
            )}
          </div>

          <div className="grid gap-3 border-t border-white/10 p-4 sm:grid-cols-3">
            <InfoCard
              eyebrow="1 · Select"
              title="Pixel mask"
              text="Pause at any moment. SAM3 highlights every instance of the named concept."
            />
            <InfoCard
              eyebrow="2 · Track"
              title="Object identity"
              text="SAM2 propagates the selected mask across the chosen clip."
            />
            <InfoCard
              eyebrow="3 · Reconstruct"
              title="Clean background"
              text="ProPainter fills the temporal hole; a new MP4 is created."
            />
          </div>
        </section>

        <aside className="flex min-h-[680px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#0b0e13] shadow-2xl">
          <div className="border-b border-white/10 px-4 py-3">
            <div className="text-sm font-semibold">Object removal</div>
            <div className="text-[11px] text-neutral-500">
              Personal non-commercial ProPainter backend · original never modified
            </div>
          </div>

          <div className="flex-1 space-y-5 overflow-y-auto p-4">
            <Step number="1" title="Name what to highlight">
              <p className="mb-3 text-xs leading-5 text-neutral-500">
                SAM3 is concept-based: it finds every instance of one concept at
                the paused moment. “Object” alone is too broad; choose something concrete.
              </p>
              <div className="flex gap-2">
                <input
                  value={concept}
                  onChange={(event) => setConcept(event.target.value)}
                  className="min-w-0 flex-1 rounded-lg border border-white/10 bg-neutral-900 px-3 py-2 text-sm outline-none focus:border-emerald-400/50"
                  placeholder="person, flag, chair…"
                />
                <button
                  onClick={() => void highlight()}
                  disabled={!active || active.status !== "ready" || discovering || !concept.trim()}
                  className="rounded-lg bg-emerald-400 px-3 text-sm font-semibold text-neutral-950 disabled:bg-neutral-800 disabled:text-neutral-600"
                >
                  {discovering ? "Finding…" : "Highlight"}
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {CONCEPTS.map((item) => (
                  <button
                    key={item}
                    onClick={() => setConcept(item)}
                    className={`rounded-full border px-2 py-1 text-[10px] ${
                      item === concept
                        ? "border-emerald-400/40 bg-emerald-400/10 text-emerald-300"
                        : "border-white/10 text-neutral-500"
                    }`}
                  >
                    {item}
                  </button>
                ))}
              </div>
            </Step>

            <Step number="2" title="Click one highlighted object">
              {!discovery && (
                <div className="rounded-xl border border-dashed border-white/10 p-4 text-center text-xs text-neutral-600">
                  Pause the video, then highlight a concept.
                </div>
              )}
              {discovery && discovery.instances.length === 0 && (
                <div className="rounded-xl bg-amber-400/5 p-3 text-xs text-amber-200/70">
                  No “{discovery.concept}” found above 0.30 confidence at{" "}
                  {clock(discovery.timestamp_s)}. Try another moment or concept.
                </div>
              )}
              {discovery && discovery.instances.length > 0 && (
                <>
                  <div className="mb-2 text-xs text-neutral-500">
                    {discovery.instances.length} found at {clock(discovery.timestamp_s)}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {discovery.instances.map((instance, index) => (
                      <button
                        key={`${index}-${instance.score}`}
                        onClick={() => selectObject(index)}
                        className={`rounded-lg border p-2 text-left ${
                          selectedIndices.includes(index)
                            ? "border-rose-400 bg-rose-400/10"
                            : "border-white/10 bg-white/[0.025] hover:border-emerald-400/40"
                        }`}
                      >
                        <div className="text-xs font-medium">
                          {discovery.concept} {index + 1}
                        </div>
                        <div className="mt-1 text-[10px] text-neutral-500">
                          {(instance.score * 100).toFixed(0)}% confidence
                        </div>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </Step>

            <Step number="3" title="Choose the edit range">
              <label className="mb-3 flex cursor-pointer items-start gap-2 rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-3">
                <input
                  type="checkbox"
                  checked={fullVideo}
                  onChange={(event) => {
                    const checked = event.target.checked;
                    setFullVideo(checked);
                    if (checked && active?.duration_s != null) {
                      setClipStart(0);
                      setClipEnd(active.duration_s);
                    } else if (discovery) {
                      const start = Math.max(0, discovery.timestamp_s - 2.5);
                      setClipStart(Number(start.toFixed(2)));
                      setClipEnd(
                        Number(
                          Math.min(active?.duration_s ?? start + 5, start + 5).toFixed(2),
                        ),
                      );
                    }
                  }}
                  className="mt-0.5 accent-emerald-400"
                />
                <span>
                  <span className="block text-xs font-semibold text-emerald-300">
                    Remove through full video
                  </span>
                  <span className="mt-1 block text-[10px] leading-4 text-neutral-500">
                    Keeps source duration, dimensions, frame rate and audio. A
                    2-minute video may take 45–120 minutes on this Mac.
                  </span>
                </span>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <TimeInput
                  label="Start"
                  value={clipStart}
                  onChange={setClipStart}
                  disabled={fullVideo}
                />
                <TimeInput
                  label="End"
                  value={clipEnd}
                  onChange={setClipEnd}
                  disabled={fullVideo}
                />
              </div>
              <div className="mt-2 flex items-center justify-between text-[10px] text-neutral-500">
                <span>Selected frame: {clock(discovery?.timestamp_s)}</span>
                <span>
                  {Math.max(0, clipEnd - clipStart).toFixed(1)}s
                  {fullVideo ? " · full source" : " · quick test"}
                </span>
              </div>
              <div className="mt-3 rounded-lg bg-sky-400/5 p-2.5 text-[11px] leading-5 text-sky-200/60">
                Source-quality composite: original pixels stay unchanged outside
                the tracked mask. Only the removed region is reconstructed, then
                the full frame is encoded at H.264 CRF 16. Original audio is restored.
              </div>
            </Step>

            {!removal && (
              <button
                onClick={() => void removeSelected()}
                disabled={
                  selected.length === 0 ||
                  submittingRemoval ||
                  !propainterReady ||
                  clipEnd <= clipStart ||
                  clipEnd - clipStart > 600 ||
                  !discovery ||
                  discovery.timestamp_s < clipStart ||
                  discovery.timestamp_s > clipEnd
                }
                className="w-full rounded-xl bg-rose-500 py-3 text-sm font-semibold text-white hover:bg-rose-400 disabled:bg-neutral-800 disabled:text-neutral-600"
              >
                {submittingRemoval
                  ? "Starting removal…"
                  : selected.length > 0
                    ? `Remove ${selected.length} selected ${
                        selected.length === 1
                          ? discovery?.concept
                          : `${discovery?.concept}s`
                      }`
                    : "Select one or more objects to remove"}
              </button>
            )}

            {removal && (
              <RemovalProgress
                removal={removal}
                onCancel={async () => {
                  await api.cancelShowcaseRemoval(removal.id);
                  setRemoval(null);
                  setError(null);
                }}
              />
            )}
            {!propainterReady && (
              <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-3 text-xs leading-5 text-amber-200/70">
                Personal worker is offline. Run:
                <code className="mt-2 block rounded bg-black/30 p-2 text-[10px] text-amber-100">
                  python3 scripts/personal_video_worker.py
                </code>
              </div>
            )}
            {error && <div className="text-xs text-rose-400">{error}</div>}
          </div>
        </aside>
      </div>
    </div>
  );
}

function ObjectOverlay({
  discovery,
  selectedIndices,
  onSelect,
}: {
  discovery: ShowcaseDiscovery;
  selectedIndices: number[];
  onSelect: (index: number) => void;
}) {
  const palette = ["#34d399", "#60a5fa", "#fbbf24", "#a78bfa", "#22d3ee"];
  return (
    <svg
      className="pointer-events-none h-full w-full"
      viewBox={`0 0 ${discovery.width} ${discovery.height}`}
      preserveAspectRatio="xMidYMid meet"
    >
      {discovery.instances.map((instance, index) => {
        const selected = selectedIndices.includes(index);
        const color = selected ? "#fb7185" : palette[index % palette.length];
        return (
          <polygon
            key={`${index}-${instance.score}`}
            points={instance.polygon.map(([x, y]) => `${x},${y}`).join(" ")}
            fill={color}
            fillOpacity={selected ? 0.48 : 0.27}
            stroke={color}
            strokeWidth={selected ? 5 : 3}
            className="pointer-events-auto cursor-pointer transition hover:fill-opacity-50"
            onClick={() => onSelect(index)}
          />
        );
      })}
    </svg>
  );
}

function contentRect(width: number, height: number) {
  // The stage is 16:9. Match video object-contain letterboxing exactly.
  const sourceAspect = width / height;
  const stageAspect = 16 / 9;
  if (sourceAspect >= stageAspect) {
    return {
      width: "100%",
      height: `${(stageAspect / sourceAspect) * 100}%`,
      transform: "translate(-50%, -50%)",
    };
  }
  return {
    width: `${(sourceAspect / stageAspect) * 100}%`,
    height: "100%",
    transform: "translate(-50%, -50%)",
  };
}

function RemovalProgress({
  removal,
  onCancel,
}: {
  removal: ShowcaseRemoval;
  onCancel: () => Promise<void>;
}) {
  const percent = Math.round(removal.progress * 100);
  const [elapsed, setElapsed] = useState(removal.elapsed_seconds);
  useEffect(() => {
    setElapsed(removal.elapsed_seconds);
    if (["completed", "failed", "cancelled"].includes(removal.status)) return;
    const baseline = removal.elapsed_seconds;
    const started = Date.now();
    const timer = window.setInterval(() => {
      setElapsed(baseline + (Date.now() - started) / 1000);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [removal.id, removal.status, removal.elapsed_seconds]);
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
      {removal.status === "completed" ? (
        <>
          <div className="text-sm font-semibold text-emerald-300">New MP4 ready</div>
          <div className="mt-1 text-xs text-neutral-500">
            Original preserved · edited clip is a separate derived file.
          </div>
          <div className="mt-2 text-xs font-medium text-emerald-200">
            Total processing time: {duration(elapsed)}
          </div>
          {removal.output_url && (
            <a
              href={fileUrl(removal.output_url)}
              target="_blank"
              rel="noreferrer"
              className="mt-3 block rounded-lg bg-emerald-400 px-3 py-2.5 text-center text-sm font-semibold text-neutral-950"
            >
              Open edited MP4 in new tab
            </a>
          )}
        </>
      ) : removal.status === "failed" || removal.status === "cancelled" ? (
        <>
          <div className="text-sm font-semibold text-rose-300">
            {removal.status === "cancelled" ? "Removal cancelled" : "Removal failed"}
          </div>
          <div className="mt-2 text-xs leading-5 text-rose-200/60">{removal.error}</div>
        </>
      ) : (
        <>
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium capitalize text-neutral-300">{removal.stage}</span>
            <span className="text-neutral-500">
              {percent}% · {duration(elapsed)}
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-neutral-800">
            <div
              className="h-full rounded-full bg-emerald-400 transition-all"
              style={{ width: `${percent}%` }}
            />
          </div>
          <div className="mt-2 text-[10px] leading-4 text-neutral-500">
            {removal.detail ?? "Queued on the local MPS worker"}
          </div>
          <button
            onClick={() => void onCancel()}
            className="mt-3 rounded-lg border border-rose-400/20 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-400/10"
          >
            Cancel job
          </button>
        </>
      )}
    </div>
  );
}

function RuntimePill({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <span
      className={`rounded-full border px-3 py-1.5 ${
        ok
          ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
          : "border-rose-400/20 bg-rose-400/10 text-rose-300"
      }`}
    >
      {ok ? "●" : "○"} {label} <span className="opacity-60">· {detail}</span>
    </span>
  );
}

function EmptyVideo({
  active,
  uploading,
  onLoad,
}: {
  active: ShowcaseVideo | null;
  uploading: boolean;
  onLoad: () => void;
}) {
  const preparing = active?.status === "preparing";
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center bg-[radial-gradient(circle_at_center,_#15241e_0,_#050606_60%)] p-8 text-center">
      <div className="mb-4 grid h-16 w-16 place-items-center rounded-2xl border border-emerald-400/20 bg-emerald-400/10 text-3xl">
        {preparing ? "◌" : "▶"}
      </div>
      <div className="text-lg font-semibold">
        {preparing ? "Preparing browser playback…" : "Load a video to begin"}
      </div>
      <div className="mt-1 max-w-md text-sm text-neutral-500">
        {preparing
          ? "The original is being copied and transcoded. It is never overwritten."
          : "MOV, MP4, M4V, AVI or WebM · up to 1 GB · original preserved"}
      </div>
      {!preparing && (
        <button
          onClick={onLoad}
          disabled={uploading}
          className="mt-5 rounded-xl bg-emerald-400 px-5 py-2.5 text-sm font-semibold text-neutral-950"
        >
          Choose video
        </button>
      )}
    </div>
  );
}

function Step({
  number,
  title,
  children,
}: {
  number: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center gap-2">
        <span className="grid h-5 w-5 place-items-center rounded-full bg-emerald-400/10 text-[10px] font-bold text-emerald-300">
          {number}
        </span>
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function TimeInput({
  label,
  value,
  onChange,
  disabled = false,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}) {
  return (
    <label className="text-[10px] uppercase tracking-wider text-neutral-500">
      {label} (seconds)
      <input
        type="number"
        min={0}
        step={0.1}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        disabled={disabled}
        className="mt-1 w-full rounded-lg border border-white/10 bg-neutral-900 px-2 py-2 text-sm normal-case tracking-normal text-neutral-100 outline-none disabled:text-neutral-600"
      />
    </label>
  );
}

function InfoCard({ eyebrow, title, text }: { eyebrow: string; title: string; text: string }) {
  return (
    <div className="rounded-xl border border-white/5 bg-white/[0.025] p-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-emerald-400/70">
        {eyebrow}
      </div>
      <div className="mt-1 text-sm font-semibold">{title}</div>
      <div className="mt-1 text-xs leading-relaxed text-neutral-500">{text}</div>
    </div>
  );
}
