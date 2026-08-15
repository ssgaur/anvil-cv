import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, fileUrl } from "../lib/api";
import type {
  ShowcaseChatResult,
  ShowcaseRuntime,
  ShowcaseVideo,
} from "../types";

type Message =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; text: string; result?: ShowcaseChatResult };

const QUICK_QUESTIONS = [
  "What is happening in this video?",
  "Who or what is most important here?",
  "Why might this activity be happening?",
  "What visible objects appear?",
  "How does the scene change over time?",
];

function welcomeMessage(): Message {
  return {
    id: id(),
    role: "assistant",
    text:
      "Ask me a visual question in English. I sample key moments with local Gemma, " +
      "then SAM3 segments the best evidence frame so the answer is grounded.",
  };
}

function id() {
  return crypto.randomUUID();
}

function clock(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function bytes(value: number) {
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function ModelShowcasePage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const uploadRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const [runtime, setRuntime] = useState<ShowcaseRuntime | null>(null);
  const [videos, setVideos] = useState<ShowcaseVideo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>(() => [welcomeMessage()]);

  const active = videos.find((video) => video.id === activeId) ?? videos[0] ?? null;

  async function load() {
    const [nextRuntime, nextVideos] = await Promise.all([
      api.showcaseRuntime(),
      api.listShowcaseVideos(),
    ]);
    setRuntime(nextRuntime);
    setVideos((current) =>
      nextVideos.map((next) => {
        const previous = current.find((item) => item.id === next.id);
        if (previous?.status === "ready" && next.status === "ready") {
          // Polling another preparing video must not mint a new active video
          // URL every 2.5s and reset browser playback.
          return {
            ...next,
            playback_url: previous.playback_url,
            poster_url: previous.poster_url,
          };
        }
        return next;
      }),
    );
    setActiveId((current) => current ?? nextVideos[0]?.id ?? null);
  }

  useEffect(() => {
    load().catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  useEffect(() => {
    if (!videos.some((video) => video.status === "preparing")) return;
    const timer = window.setInterval(() => {
      load().catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [videos]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    setMessages([welcomeMessage()]);
    setQuestion("");
  }, [activeId]);

  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return;
    }
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [busy]);

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

  async function ask(raw: string) {
    const text = raw.trim();
    if (!text || !active || active.status !== "ready" || busy) return;
    setQuestion("");
    setError(null);
    setMessages((current) => [...current, { id: id(), role: "user", text }]);
    setBusy(true);
    try {
      const result = await api.chatAboutShowcaseVideo(active.id, {
        question: text,
        current_time_s: videoRef.current?.currentTime ?? null,
      });
      setMessages((current) => [
        ...current,
        { id: id(), role: "assistant", text: result.answer, result },
      ]);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setMessages((current) => [
        ...current,
        {
          id: id(),
          role: "assistant",
          text: `I could not finish the local visual analysis: ${message}`,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
  }

  function jump(timestamp: number | null) {
    if (timestamp == null || !videoRef.current) return;
    videoRef.current.currentTime = timestamp;
    void videoRef.current.play();
  }

  return (
    <div className="min-h-full bg-[#07090d] text-neutral-100">
      <header className="border-b border-white/10 bg-neutral-950/70 px-6 py-4 backdrop-blur">
        <div className="mx-auto flex max-w-[1700px] items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-400">
              <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_16px_#34d399]" />
              Local visual intelligence
            </div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">Ask your video</h1>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <RuntimePill ok={runtime?.sam3 ?? false} label="SAM3" detail="grounding" />
            <RuntimePill
              ok={runtime?.ollama ?? false}
              label={runtime?.ollama_model ?? "Ollama"}
              detail="reasoning"
            />
            <span className="rounded-full border border-white/10 px-3 py-1.5 text-neutral-400">
              private · on this Mac
            </span>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1700px] gap-4 p-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(420px,0.7fr)]">
        <section className="min-w-0 overflow-hidden rounded-2xl border border-white/10 bg-neutral-950 shadow-2xl shadow-black/30">
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
                  <span>{bytes(active.bytes)}</span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {videos.length > 1 && (
                <select
                  value={active?.id ?? ""}
                  onChange={(event) => setActiveId(event.target.value)}
                  disabled={busy || uploading}
                  className="max-w-48 rounded-lg border border-white/10 bg-neutral-900 px-2 py-1.5 text-xs"
                >
                  {videos.map((video) => (
                    <option value={video.id} key={video.id}>{video.filename}</option>
                  ))}
                </select>
              )}
              <button
                onClick={() => uploadRef.current?.click()}
                disabled={uploading || busy}
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
              <video
                ref={videoRef}
                className="h-full w-full"
                controls
                preload="metadata"
                poster={fileUrl(active.poster_url)}
                src={fileUrl(active.playback_url)}
              />
            ) : (
              <EmptyVideo
                active={active}
                onLoad={() => uploadRef.current?.click()}
                uploading={uploading}
              />
            )}
          </div>

          <div className="grid gap-3 border-t border-white/10 p-4 sm:grid-cols-3">
            <InfoCard
              eyebrow="How it sees"
              title="5 moments"
              text="Gemma compares the current moment with four points across the clip."
            />
            <InfoCard
              eyebrow="How it verifies"
              title="Pixel masks"
              text="SAM3 segments the concrete subject on the strongest evidence frame."
            />
            <InfoCard
              eyebrow="What stays local"
              title="Everything"
              text="Video, frames, prompts and model outputs never leave this machine."
            />
          </div>
        </section>

        <section className="flex min-h-[680px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-[#0b0e13] shadow-2xl shadow-black/30">
          <div className="border-b border-white/10 px-4 py-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold">Visual chat</div>
                <div className="text-[11px] text-neutral-500">
                  Gemma reasons · SAM3 grounds · no audio transcription
                </div>
              </div>
              <span className="rounded-full bg-emerald-400/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-emerald-300">
                English
              </span>
            </div>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {messages.map((message) => (
              <ChatBubble key={message.id} message={message} onJump={jump} />
            ))}
            {busy && <Thinking elapsed={elapsed} />}
            <div ref={chatEndRef} />
          </div>

          <div className="border-t border-white/10 bg-neutral-950/60 p-3">
            <div className="mb-2 flex gap-1.5 overflow-x-auto pb-1">
              {QUICK_QUESTIONS.map((item) => (
                <button
                  key={item}
                  onClick={() => void ask(item)}
                  disabled={busy || active?.status !== "ready"}
                  className="shrink-0 rounded-full border border-white/10 px-2.5 py-1 text-[10px] text-neutral-400 hover:border-emerald-400/40 hover:text-emerald-300 disabled:opacity-40"
                >
                  {item}
                </button>
              ))}
            </div>
            <form onSubmit={submit} className="flex gap-2">
              <textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void ask(question);
                  }
                }}
                placeholder="Ask what, who, where, when, why, or how…"
                rows={2}
                disabled={busy || active?.status !== "ready"}
                className="min-h-[54px] flex-1 resize-none rounded-xl border border-white/10 bg-neutral-900 px-3 py-2 text-sm outline-none placeholder:text-neutral-600 focus:border-emerald-400/50 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={busy || !question.trim() || active?.status !== "ready"}
                className="self-stretch rounded-xl bg-emerald-400 px-4 text-sm font-semibold text-neutral-950 hover:bg-emerald-300 disabled:bg-neutral-800 disabled:text-neutral-600"
              >
                Ask
              </button>
            </form>
            {error && <div className="mt-2 text-xs text-rose-400">{error}</div>}
          </div>
        </section>
      </div>
    </div>
  );
}

function RuntimePill({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <span className={`rounded-full border px-3 py-1.5 ${
      ok
        ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300"
        : "border-rose-400/20 bg-rose-400/10 text-rose-300"
    }`}>
      {ok ? "●" : "○"} {label} <span className="opacity-60">· {detail}</span>
    </span>
  );
}

function EmptyVideo({
  active,
  onLoad,
  uploading,
}: {
  active: ShowcaseVideo | null;
  onLoad: () => void;
  uploading: boolean;
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
          ? "The original HEVC video is being converted locally to streamable H.264."
          : "MOV, MP4, M4V, AVI or WebM · up to 1 GB · never uploaded to a cloud service"}
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
      {active?.status === "failed" && (
        <div className="mt-4 max-w-lg text-xs text-rose-400">{active.error}</div>
      )}
    </div>
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

function ChatBubble({
  message,
  onJump,
}: {
  message: Message;
  onJump: (timestamp: number | null) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="ml-10 rounded-2xl rounded-br-md bg-emerald-400 px-3.5 py-2.5 text-sm text-neutral-950">
        {message.text}
      </div>
    );
  }
  const result = message.result;
  return (
    <div className="mr-4 rounded-2xl rounded-bl-md border border-white/10 bg-white/[0.035] p-3.5">
      <p className="whitespace-pre-wrap text-sm leading-6 text-neutral-200">{message.text}</p>
      {result && (
        <>
          <div className="mt-3 flex flex-wrap gap-1.5 text-[10px]">
            <span className="rounded-full bg-sky-400/10 px-2 py-1 text-sky-300">
              {result.confidence} confidence
            </span>
            {result.grounding_concept && (
              <span className="rounded-full bg-violet-400/10 px-2 py-1 text-violet-300">
                SAM3: {result.grounding_concept} · {result.detection_count} found
              </span>
            )}
            <span className="rounded-full bg-white/5 px-2 py-1 text-neutral-500">
              {result.ollama_model}
            </span>
          </div>
          {result.evidence_url && (
            <button
              onClick={() => onJump(result.evidence_timestamp_s)}
              className="group mt-3 block w-full overflow-hidden rounded-xl border border-white/10 text-left"
            >
              <img
                src={fileUrl(result.evidence_url)}
                alt={`SAM3 evidence for ${result.grounding_concept ?? "answer"}`}
                className="aspect-video w-full object-cover transition group-hover:scale-[1.01]"
              />
              <div className="flex items-center justify-between bg-neutral-950/80 px-3 py-2 text-xs">
                <span className="text-neutral-400">
                  SAM3 evidence · {clock(result.evidence_timestamp_s)}
                </span>
                <span className="text-emerald-300">Jump to moment →</span>
              </div>
            </button>
          )}
          {result.caveat && (
            <div className="mt-3 border-l-2 border-amber-400/30 pl-2 text-xs leading-5 text-amber-200/60">
              {result.caveat}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Thinking({ elapsed }: { elapsed: number }) {
  const phase =
    elapsed < 8
      ? "Sampling five moments from the video"
      : elapsed < 35
        ? "Gemma is comparing visual evidence"
        : "SAM3 is grounding the strongest frame";
  return (
    <div className="mr-8 rounded-2xl rounded-bl-md border border-emerald-400/10 bg-emerald-400/[0.035] p-3">
      <div className="flex items-center gap-2 text-xs text-emerald-300">
        <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
        {phase}
      </div>
      <div className="mt-1 text-[10px] text-neutral-600">
        Local CPU inference · {elapsed}s elapsed
      </div>
    </div>
  );
}
