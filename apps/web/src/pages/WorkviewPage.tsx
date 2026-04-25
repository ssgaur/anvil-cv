import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Image as KImage, Layer, Line, Rect, Stage } from "react-konva";
import Konva from "konva";
import { api, fileUrl } from "../lib/api";
import type { Annotation, ClassRow, ImageRow, ModelVersion } from "../types";

type Tool = "pan" | "auto" | "polygon" | "bbox";

export function WorkviewPage() {
  const { projectId = "", datasetId = "", imageId = "" } = useParams();
  const nav = useNavigate();

  // Data
  const [image, setImage] = useState<ImageRow | null>(null);
  const [classes, setClasses] = useState<ClassRow[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [siblings, setSiblings] = useState<ImageRow[]>([]);
  const [htmlImage, setHtmlImage] = useState<HTMLImageElement | null>(null);

  // Tool state
  const [tool, setTool] = useState<Tool>("auto");
  const [activeClassId, setActiveClassId] = useState<string | null>(null);
  const [polyPoints, setPolyPoints] = useState<[number, number][]>([]);
  const [bboxStart, setBboxStart] = useState<[number, number] | null>(null);
  const [bboxEnd, setBboxEnd] = useState<[number, number] | null>(null);
  const [pending, setPending] = useState<{
    polygon: [number, number][]; score: number; bbox: [number, number, number, number];
  } | null>(null);
  const [predicting, setPredicting] = useState(false);
  const [hoverAnnId, setHoverAnnId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Model version selection (null = base SAM2; "active" = use project default)
  const [models, setModels] = useState<ModelVersion[]>([]);
  const [activeModelId, setActiveModelId] = useState<string | null>(null);
  const [modelChoice, setModelChoice] = useState<string>("active"); // "active" | "base" | <mv_id>

  useEffect(() => {
    api.listModelVersions(projectId).then(setModels).catch(() => {});
    api.getProject(projectId).then((p) => setActiveModelId(p.active_model_version_id ?? null)).catch(() => {});
  }, [projectId]);

  // Resolve which model_version_id to send with predict
  const predictModelId = useMemo<string | null | undefined>(() => {
    if (modelChoice === "base") return null;            // explicit base SAM2
    if (modelChoice === "active") return undefined;     // server picks project active
    return modelChoice;                                  // explicit ft version
  }, [modelChoice]);

  // Canvas state
  const stageRef = useRef<Konva.Stage>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 });
  const [stageSize, setStageSize] = useState({ w: 800, h: 600 });
  const [imgStartTime] = useState(Date.now());
  const [elapsed, setElapsed] = useState(0);

  // ---- Data load
  const loadAll = useCallback(async () => {
    const [img, cs, ann] = await Promise.all([
      api.getImage(imageId),
      api.listClasses(projectId),
      api.listAnnotations(imageId),
    ]);
    setImage(img); setClasses(cs); setAnnotations(ann);
    if (cs.length && !activeClassId) setActiveClassId(cs[0].id);
  }, [imageId, projectId, activeClassId]);

  useEffect(() => { loadAll(); /* eslint-disable-next-line */ }, [imageId]);

  // Sibling images (for nav strip)
  useEffect(() => {
    api.listImages(projectId, datasetId, { limit: 500 }).then(setSiblings);
  }, [projectId, datasetId]);

  // Poll if image not ready
  useEffect(() => {
    if (!image || image.status === "ready" || image.status === "failed") return;
    const t = setInterval(async () => {
      const img = await api.getImage(imageId);
      setImage(img);
      if (img.status === "ready" || img.status === "failed") clearInterval(t);
    }, 1500);
    return () => clearInterval(t);
  }, [image, imageId]);

  // Load HTML image
  useEffect(() => {
    if (!image) return;
    const el = new window.Image();
    el.crossOrigin = "anonymous";
    el.src = fileUrl(image.image_url);
    el.onload = () => setHtmlImage(el);
  }, [image]);

  // Stage size
  useEffect(() => {
    function resize() {
      if (!containerRef.current) return;
      setStageSize({
        w: containerRef.current.clientWidth,
        h: containerRef.current.clientHeight,
      });
    }
    resize();
    const ro = new ResizeObserver(resize);
    if (containerRef.current) ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  // Fit-to-screen on image load
  useEffect(() => {
    if (!htmlImage || !stageSize.w || !stageSize.h) return;
    const scale = Math.min(stageSize.w / htmlImage.width, stageSize.h / htmlImage.height) * 0.95;
    setView({
      scale,
      x: (stageSize.w - htmlImage.width * scale) / 2,
      y: (stageSize.h - htmlImage.height * scale) / 2,
    });
  }, [htmlImage, stageSize]);

  // Per-image timer
  useEffect(() => {
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - imgStartTime) / 1000)), 1000);
    return () => clearInterval(t);
  }, [imgStartTime]);

  // Keyboard shortcuts
  useEffect(() => {
    function k(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "v") setTool("pan");
      else if (e.key === "a") setTool("auto");
      else if (e.key === "p") setTool("polygon");
      else if (e.key === "b") setTool("bbox");
      else if (e.key === "Escape") { setPolyPoints([]); setPending(null); setBboxStart(null); setBboxEnd(null); }
      else if (e.key === "Enter" && tool === "polygon" && polyPoints.length >= 3) commitPolygon();
      else if (e.key === "Delete" || e.key === "Backspace") {
        if (hoverAnnId) deleteAnnotation(hoverAnnId);
      } else if (e.key === "ArrowRight") gotoSibling(1);
      else if (e.key === "ArrowLeft") gotoSibling(-1);
    }
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool, polyPoints, hoverAnnId, siblings, imageId]);

  // Helpers
  function imagePoint(stageX: number, stageY: number): [number, number] {
    return [(stageX - view.x) / view.scale, (stageY - view.y) / view.scale];
  }

  function gotoSibling(delta: number) {
    if (siblings.length === 0) return;
    const idx = siblings.findIndex((s) => s.id === imageId);
    const next = siblings[(idx + delta + siblings.length) % siblings.length];
    if (next) nav(`/projects/${projectId}/datasets/${datasetId}/workview/${next.id}`);
  }

  // Wheel zoom
  function onWheel(e: Konva.KonvaEventObject<WheelEvent>) {
    e.evt.preventDefault();
    const stage = stageRef.current;
    if (!stage) return;
    const oldScale = view.scale;
    const pointer = stage.getPointerPosition();
    if (!pointer) return;
    const factor = e.evt.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.max(0.05, Math.min(20, oldScale * factor));
    const mx = (pointer.x - view.x) / oldScale;
    const my = (pointer.y - view.y) / oldScale;
    setView({
      scale: newScale,
      x: pointer.x - mx * newScale,
      y: pointer.y - my * newScale,
    });
  }

  // Click
  async function onStageClick(_e: Konva.KonvaEventObject<MouseEvent>) {
    const stage = stageRef.current; if (!stage) return;
    const pos = stage.getPointerPosition(); if (!pos) return;
    const [ix, iy] = imagePoint(pos.x, pos.y);
    if (!image) return;
    if (ix < 0 || iy < 0 || ix > image.width || iy > image.height) return;

    if (tool === "auto") {
      if (image.status !== "ready") {
        setError("Image is still embedding. Please wait a few seconds.");
        return;
      }
      if (!activeClassId) { setError("Pick a class on the right first."); return; }
      setPredicting(true); setError(null);
      try {
        const r = await api.predict(imageId, {
          points: [[ix, iy]], point_labels: [1],
          ...(predictModelId !== undefined ? { model_version_id: predictModelId } : {}),
        });
        setPending({ polygon: r.polygon, score: r.score, bbox: r.bbox });
      } catch (err) {
        setError(err instanceof Error ? err.message : "predict failed");
      } finally { setPredicting(false); }
    } else if (tool === "polygon") {
      setPolyPoints([...polyPoints, [ix, iy]]);
    }
  }

  // Bbox drag
  function onMouseDown(_e: Konva.KonvaEventObject<MouseEvent>) {
    if (tool !== "bbox") return;
    const stage = stageRef.current; if (!stage) return;
    const p = stage.getPointerPosition(); if (!p) return;
    setBboxStart(imagePoint(p.x, p.y));
    setBboxEnd(imagePoint(p.x, p.y));
  }
  function onMouseMove() {
    if (tool !== "bbox" || !bboxStart) return;
    const stage = stageRef.current; if (!stage) return;
    const p = stage.getPointerPosition(); if (!p) return;
    setBboxEnd(imagePoint(p.x, p.y));
  }
  async function onMouseUp() {
    if (tool === "bbox" && bboxStart && bboxEnd && image && activeClassId) {
      const x = Math.min(bboxStart[0], bboxEnd[0]);
      const y = Math.min(bboxStart[1], bboxEnd[1]);
      const w = Math.abs(bboxEnd[0] - bboxStart[0]);
      const h = Math.abs(bboxEnd[1] - bboxStart[1]);
      setBboxStart(null); setBboxEnd(null);
      if (w < 4 || h < 4) return;
      try {
        const a = await api.createAnnotation(imageId, {
          class_id: activeClassId,
          geometry_type: "bbox",
          geometry: { x, y, w, h },
          source: "manual",
        });
        setAnnotations((arr) => [...arr, a]);
      } catch (err) { setError(err instanceof Error ? err.message : "save failed"); }
    }
  }

  async function commitPolygon() {
    if (!image || !activeClassId || polyPoints.length < 3) return;
    try {
      const a = await api.createAnnotation(imageId, {
        class_id: activeClassId,
        geometry_type: "polygon",
        geometry: { points: polyPoints },
        source: "manual",
      });
      setAnnotations((arr) => [...arr, a]);
      setPolyPoints([]);
    } catch (err) { setError(err instanceof Error ? err.message : "save failed"); }
  }

  async function commitPending() {
    if (!image || !activeClassId || !pending) return;
    try {
      const a = await api.createAnnotation(imageId, {
        class_id: activeClassId,
        geometry_type: "polygon",
        geometry: { points: pending.polygon },
        source: "model",
        confidence: pending.score,
      });
      setAnnotations((arr) => [...arr, a]);
      setPending(null);
    } catch (err) { setError(err instanceof Error ? err.message : "save failed"); }
  }

  async function deleteAnnotation(id: string) {
    await api.deleteAnnotation(id);
    setAnnotations((arr) => arr.filter((a) => a.id !== id));
  }

  const classMap = useMemo(() => {
    const m = new Map<string, ClassRow>();
    classes.forEach((c) => m.set(c.id, c));
    return m;
  }, [classes]);

  const activeClass = activeClassId ? classMap.get(activeClassId) : null;

  const idx = siblings.findIndex((s) => s.id === imageId);

  return (
    <div className="h-screen flex flex-col bg-neutral-950 text-neutral-100">
      {/* Top bar */}
      <div className="h-12 border-b border-neutral-800 flex items-center px-4 gap-4 text-sm bg-neutral-900">
        <Link to={`/projects/${projectId}/datasets/${datasetId}`} className="text-neutral-400 hover:text-white">
          ← Back to dataset
        </Link>
        <div className="text-neutral-500">|</div>
        <div className="flex-1 truncate font-medium">
          {image?.filename ?? "loading…"}{" "}
          {image && (
            <span className="text-xs text-neutral-500 ml-2">
              {image.width}×{image.height} · {(image.bytes / 1024).toFixed(0)}KB · {image.status}
            </span>
          )}
        </div>
        <div className="text-xs text-neutral-400">⏱ {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}</div>
        <ModelPicker
          models={models}
          activeModelId={activeModelId}
          choice={modelChoice}
          onChange={setModelChoice}
        />
        <div className="text-xs text-neutral-400">{idx + 1} / {siblings.length}</div>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left toolbar */}
        <div className="w-14 bg-neutral-900 border-r border-neutral-800 flex flex-col items-center py-3 gap-2">
          <ToolBtn active={tool === "pan"} onClick={() => setTool("pan")} label="Pan (V)" icon="✋" />
          <ToolBtn active={tool === "auto"} onClick={() => setTool("auto")} label="Auto-Annotate (A) — click to segment with SAM2" icon="✨" />
          <ToolBtn active={tool === "polygon"} onClick={() => setTool("polygon")} label="Polygon (P)" icon="⬡" />
          <ToolBtn active={tool === "bbox"} onClick={() => setTool("bbox")} label="Bounding Box (B)" icon="▭" />
        </div>

        {/* Canvas */}
        <div ref={containerRef} className="flex-1 relative bg-neutral-800 overflow-hidden">
          {predicting && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-emerald-500 text-neutral-950 text-xs px-3 py-1 rounded-full font-medium z-10">
              SAM2 predicting…
            </div>
          )}
          {error && (
            <div className="absolute top-3 right-3 bg-rose-500 text-white text-xs px-3 py-2 rounded z-10 max-w-xs">
              {error} <button onClick={() => setError(null)} className="ml-2 font-bold">×</button>
            </div>
          )}
          {image && image.status !== "ready" && tool === "auto" && (
            <div className="absolute bottom-20 left-1/2 -translate-x-1/2 bg-amber-500 text-neutral-950 text-xs px-3 py-1 rounded z-10">
              Embedding… auto-annotate ready in a moment
            </div>
          )}

          <Stage
            ref={stageRef}
            width={stageSize.w}
            height={stageSize.h}
            onWheel={onWheel}
            onClick={onStageClick}
            onMouseDown={onMouseDown}
            onMouseMove={onMouseMove}
            onMouseUp={onMouseUp}
            draggable={tool === "pan"}
            onDragEnd={(e) => {
              if (tool === "pan") setView({ ...view, x: e.target.x(), y: e.target.y() });
            }}
            x={view.x}
            y={view.y}
            scaleX={view.scale}
            scaleY={view.scale}
            style={{
              cursor: tool === "pan" ? "grab" : tool === "auto" ? "crosshair" : "crosshair",
              background: "#171717",
            }}
          >
            <Layer>
              {htmlImage && (
                <KImage image={htmlImage} x={0} y={0} width={htmlImage.width} height={htmlImage.height} />
              )}
            </Layer>

            {/* Existing annotations */}
            <Layer listening>
              {annotations.map((a) => {
                const cls = classMap.get(a.class_id);
                const color = cls?.color ?? "#22c55e";
                if (a.geometry_type === "polygon" && a.geometry.points) {
                  const flat = a.geometry.points.flat();
                  return (
                    <Line
                      key={a.id}
                      points={flat}
                      closed
                      fill={color + "33"}
                      stroke={color}
                      strokeWidth={2 / view.scale}
                      onMouseEnter={() => setHoverAnnId(a.id)}
                      onMouseLeave={() => setHoverAnnId(null)}
                    />
                  );
                }
                if (a.geometry_type === "bbox" && a.geometry.x !== undefined) {
                  return (
                    <Rect
                      key={a.id}
                      x={a.geometry.x} y={a.geometry.y!}
                      width={a.geometry.w!} height={a.geometry.h!}
                      stroke={color}
                      strokeWidth={2 / view.scale}
                      fill={color + "22"}
                      onMouseEnter={() => setHoverAnnId(a.id)}
                      onMouseLeave={() => setHoverAnnId(null)}
                    />
                  );
                }
                return null;
              })}
            </Layer>

            {/* Pending (in-progress) */}
            <Layer listening={false}>
              {pending && activeClass && (
                <Line
                  points={pending.polygon.flat()}
                  closed
                  fill={activeClass.color + "55"}
                  stroke={activeClass.color}
                  strokeWidth={2 / view.scale}
                  dash={[6 / view.scale, 4 / view.scale]}
                />
              )}
              {polyPoints.length > 0 && activeClass && (
                <Line
                  points={polyPoints.flat()}
                  stroke={activeClass.color}
                  strokeWidth={2 / view.scale}
                  closed={false}
                />
              )}
              {bboxStart && bboxEnd && activeClass && (
                <Rect
                  x={Math.min(bboxStart[0], bboxEnd[0])}
                  y={Math.min(bboxStart[1], bboxEnd[1])}
                  width={Math.abs(bboxEnd[0] - bboxStart[0])}
                  height={Math.abs(bboxEnd[1] - bboxStart[1])}
                  stroke={activeClass.color}
                  strokeWidth={2 / view.scale}
                  dash={[6 / view.scale, 4 / view.scale]}
                />
              )}
            </Layer>
          </Stage>

          {/* Pending action bar */}
          {pending && (
            <div className="absolute bottom-24 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 rounded-lg p-2 flex gap-2 items-center text-sm shadow-xl z-10">
              <span className="text-emerald-400 px-2">SAM2 score: {pending.score.toFixed(3)}</span>
              <button onClick={commitPending} className="bg-emerald-500 text-neutral-950 px-3 py-1 rounded font-medium hover:bg-emerald-400">
                Save (Enter)
              </button>
              <button onClick={() => setPending(null)} className="text-neutral-400 px-2 hover:text-white">
                Discard (Esc)
              </button>
            </div>
          )}
          {tool === "polygon" && polyPoints.length >= 3 && (
            <div className="absolute bottom-24 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-xs text-neutral-300 z-10">
              {polyPoints.length} points · press Enter to save · Esc to clear
            </div>
          )}

          {/* Bottom thumbnail strip */}
          <div className="absolute bottom-0 left-0 right-0 h-16 bg-neutral-900/90 border-t border-neutral-800 flex items-center gap-2 px-3 overflow-x-auto">
            <button
              onClick={() => gotoSibling(-1)}
              className="text-neutral-400 hover:text-white text-lg px-2"
              title="Previous (←)"
            >‹</button>
            {siblings.slice(Math.max(0, idx - 8), idx + 12).map((s) => (
              <Link
                key={s.id}
                to={`/projects/${projectId}/datasets/${datasetId}/workview/${s.id}`}
                className={`flex-shrink-0 w-12 h-12 rounded overflow-hidden border-2 ${
                  s.id === imageId ? "border-emerald-500" : "border-transparent hover:border-neutral-600"
                }`}
              >
                {s.thumbnail_url ? (
                  <img src={fileUrl(s.thumbnail_url)} className="w-full h-full object-cover" alt="" />
                ) : (
                  <div className="w-full h-full bg-neutral-800" />
                )}
              </Link>
            ))}
            <button
              onClick={() => gotoSibling(1)}
              className="text-neutral-400 hover:text-white text-lg px-2"
              title="Next (→)"
            >›</button>
          </div>
        </div>

        {/* Right panel */}
        <aside className="w-72 bg-neutral-900 border-l border-neutral-800 flex flex-col">
          <div className="p-3 border-b border-neutral-800">
            <div className="text-xs uppercase tracking-wider text-neutral-500 mb-2">Active class</div>
            {classes.length === 0 ? (
              <Link to={`/projects/${projectId}`} className="text-xs text-emerald-400 hover:underline">
                ⚠ No classes yet — add one in Project → Classes
              </Link>
            ) : (
              <div className="space-y-1">
                {classes.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => setActiveClassId(c.id)}
                    className={`w-full flex items-center gap-2 px-2 py-1.5 rounded text-sm text-left ${
                      activeClassId === c.id ? "bg-neutral-800 text-white" : "text-neutral-400 hover:bg-neutral-800/50"
                    }`}
                  >
                    <span className="w-3 h-3 rounded" style={{ background: c.color }} />
                    <span className="flex-1 truncate">{c.name}</span>
                    <span className="text-[10px] text-neutral-500">{c.annotation_count}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-3">
            <div className="text-xs uppercase tracking-wider text-neutral-500 mb-2">
              Annotations ({annotations.length})
            </div>
            {annotations.length === 0 ? (
              <div className="text-xs text-neutral-600">No annotations yet. Click on the image with the ✨ Auto tool.</div>
            ) : (
              <ul className="space-y-1">
                {annotations.map((a, i) => {
                  const c = classMap.get(a.class_id);
                  return (
                    <li
                      key={a.id}
                      onMouseEnter={() => setHoverAnnId(a.id)}
                      onMouseLeave={() => setHoverAnnId(null)}
                      className={`flex items-center gap-2 px-2 py-1.5 rounded text-sm group ${
                        hoverAnnId === a.id ? "bg-neutral-800" : ""
                      }`}
                    >
                      <span className="w-2.5 h-2.5 rounded" style={{ background: c?.color ?? "#888" }} />
                      <span className="flex-1 truncate">{c?.name ?? "?"} #{i + 1}</span>
                      <span className="text-[10px] text-neutral-500">{a.geometry_type}</span>
                      {a.source === "model" && (
                        <span className="text-[10px] text-emerald-400" title={`SAM2 confidence ${a.confidence}`}>✨</span>
                      )}
                      <button
                        onClick={() => deleteAnnotation(a.id)}
                        className="text-neutral-600 hover:text-rose-400 opacity-0 group-hover:opacity-100"
                      >✕</button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="p-3 border-t border-neutral-800 text-[11px] text-neutral-500 space-y-1">
            <div><kbd className="kbd">V</kbd> pan · <kbd className="kbd">A</kbd> auto · <kbd className="kbd">P</kbd> polygon · <kbd className="kbd">B</kbd> bbox</div>
            <div><kbd className="kbd">Enter</kbd> save · <kbd className="kbd">Esc</kbd> cancel · <kbd className="kbd">←/→</kbd> navigate</div>
            <div><kbd className="kbd">Wheel</kbd> zoom (toward cursor)</div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function ToolBtn({ active, onClick, label, icon }: { active: boolean; onClick: () => void; label: string; icon: string }) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`w-10 h-10 rounded grid place-items-center text-lg transition ${
        active ? "bg-emerald-500 text-neutral-950" : "text-neutral-400 hover:bg-neutral-800 hover:text-white"
      }`}
    >
      {icon}
    </button>
  );
}

function ModelPicker({
  models, activeModelId, choice, onChange,
}: {
  models: ModelVersion[];
  activeModelId: string | null;
  choice: string;
  onChange: (v: string) => void;
}) {
  const ready = models.filter((m) => m.status === "ready");
  const activeName = activeModelId ? models.find((m) => m.id === activeModelId)?.name ?? "fine-tuned" : "base SAM2";
  return (
    <select
      value={choice}
      onChange={(e) => onChange(e.target.value)}
      title="Which model to use for auto-annotate"
      className="text-xs bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-neutral-300"
    >
      <option value="active">Model: {activeName} (default)</option>
      <option value="base">Model: base SAM2</option>
      {ready.map((m) => (
        <option key={m.id} value={m.id}>
          Model: {m.name}{(m.metrics?.final_val_iou as number | undefined) !== undefined ? ` · IoU ${(m.metrics.final_val_iou as number).toFixed(2)}` : ""}
        </option>
      ))}
    </select>
  );
}
