import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Circle, Image as KImage, Layer, Line, Rect, Stage } from "react-konva";
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

  // Multi-prompt SAM2 state (shift=add+, alt=subtract-, drag=box)
  const [prompts, setPrompts] = useState<{
    points: [number, number, 0 | 1][]; // [x, y, label(1=fg, 0=bg)]
    box: [number, number, number, number] | null; // [x, y, w, h] image px
  }>({ points: [], box: null });
  const dragStartRef = useRef<[number, number] | null>(null);
  const wasDragRef = useRef(false);
  const [autoBoxPreview, setAutoBoxPreview] = useState<[number, number, number, number] | null>(null);

  // Polygon vertex edit
  const [selectedAnnId, setSelectedAnnId] = useState<string | null>(null);
  const [editPoly, setEditPoly] = useState<[number, number][] | null>(null);
  // Snapshot of the annotation taken when entering edit mode (for undo of polygon edits)
  const editBeforeRef = useRef<Annotation | null>(null);

  // ---- Undo/redo (per-image, capped at 20)
  type HistOp =
    | { kind: "create"; ann: Annotation }
    | { kind: "delete"; ann: Annotation }
    | { kind: "update"; before: Annotation; after: Annotation };
  const [undoStack, setUndoStack] = useState<HistOp[]>([]);
  const [redoStack, setRedoStack] = useState<HistOp[]>([]);
  const HIST_LIMIT = 20;
  function pushOp(op: HistOp) {
    setUndoStack((s) => [...s, op].slice(-HIST_LIMIT));
    setRedoStack([]);
  }
  // Replace every reference to oldId with newAnn (after delete-undo re-creates with a new id)
  function remapId(stack: HistOp[], oldId: string, newAnn: Annotation): HistOp[] {
    return stack.map((op) => {
      if (op.kind === "create" && op.ann.id === oldId) return { kind: "create", ann: newAnn };
      if (op.kind === "delete" && op.ann.id === oldId) return { kind: "delete", ann: newAnn };
      if (op.kind === "update" && op.before.id === oldId) {
        return { kind: "update", before: { ...op.before, id: newAnn.id }, after: { ...op.after, id: newAnn.id } };
      }
      return op;
    });
  }
  async function applyInverse(op: HistOp): Promise<HistOp> {
    // Returns the redo-op (forward equivalent).
    if (op.kind === "create") {
      // Undo a create => delete.
      await api.deleteAnnotation(op.ann.id);
      setAnnotations((arr) => arr.filter((a) => a.id !== op.ann.id));
      if (selectedAnnId === op.ann.id) { setSelectedAnnId(null); setEditPoly(null); }
      return { kind: "delete", ann: op.ann };
    }
    if (op.kind === "delete") {
      // Undo a delete => re-create (new id).
      const re = await api.createAnnotation(imageId, {
        class_id: op.ann.class_id,
        geometry_type: op.ann.geometry_type,
        geometry: op.ann.geometry,
        source: op.ann.source ?? "manual",
        confidence: op.ann.confidence ?? null,
      });
      setAnnotations((arr) => [...arr, re]);
      const oldId = op.ann.id;
      setUndoStack((s) => remapId(s, oldId, re));
      setRedoStack((s) => remapId(s, oldId, re));
      return { kind: "create", ann: re };
    }
    // update — patch back to before
    const reverted = await api.updateAnnotation(op.before.id, {
      class_id: op.before.class_id,
      geometry_type: op.before.geometry_type,
      geometry: op.before.geometry,
    });
    setAnnotations((arr) => arr.map((x) => (x.id === reverted.id ? reverted : x)));
    if (selectedAnnId === reverted.id && reverted.geometry_type === "polygon" && reverted.geometry.points) {
      setEditPoly(reverted.geometry.points.map((p: number[]) => [p[0], p[1]] as [number, number]));
    }
    return { kind: "update", before: op.after, after: op.before };
  }
  async function undo() {
    if (undoStack.length === 0) return;
    const op = undoStack[undoStack.length - 1];
    setUndoStack((s) => s.slice(0, -1));
    try {
      const forward = await applyInverse(op);
      setRedoStack((s) => [...s, forward].slice(-HIST_LIMIT));
    } catch (e) {
      setError(e instanceof Error ? `Undo failed: ${e.message}` : "Undo failed");
      setUndoStack((s) => [...s, op]); // restore
    }
  }
  async function redo() {
    if (redoStack.length === 0) return;
    const op = redoStack[redoStack.length - 1];
    setRedoStack((s) => s.slice(0, -1));
    try {
      const back = await applyInverse(op);
      setUndoStack((s) => [...s, back].slice(-HIST_LIMIT));
    } catch (e) {
      setError(e instanceof Error ? `Redo failed: ${e.message}` : "Redo failed");
      setRedoStack((s) => [...s, op]);
    }
  }

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

  // Active learning: jump to the most-uncertain unannotated image
  const [findingNext, setFindingNext] = useState(false);
  async function gotoMostUncertain() {
    setFindingNext(true);
    try {
      const r = await api.nextUncertain(projectId, datasetId);
      if (!r.image) {
        setError("No more un-annotated images in this dataset 🎉");
        return;
      }
      nav(`/projects/${projectId}/datasets/${datasetId}/workview/${r.image.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "next-uncertain failed");
    } finally {
      setFindingNext(false);
    }
  }

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

  // Reset selection / prompts when switching images
  useEffect(() => {
    setSelectedAnnId(null); setEditPoly(null);
    editBeforeRef.current = null;
    setUndoStack([]); setRedoStack([]);
    clearPrompts();
    setPolyPoints([]); setBboxStart(null); setBboxEnd(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageId]);

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
      const mod = e.metaKey || e.ctrlKey;
      if (mod && (e.key === "z" || e.key === "Z")) {
        e.preventDefault();
        if (e.shiftKey) redo(); else undo();
        return;
      }
      if (mod && (e.key === "y" || e.key === "Y")) { e.preventDefault(); redo(); return; }
      if (e.key === "v") setTool("pan");
      else if (e.key === "a") setTool("auto");
      else if (e.key === "p") setTool("polygon");
      else if (e.key === "b") setTool("bbox");
      else if (e.key === "Escape") {
        setPolyPoints([]); setBboxStart(null); setBboxEnd(null);
        clearPrompts();
        setSelectedAnnId(null); setEditPoly(null);
      }
      else if (e.key === "Enter" && tool === "polygon" && polyPoints.length >= 3) commitPolygon();
      else if (e.key === "Delete" || e.key === "Backspace") {
        if (hoverAnnId) deleteAnnotation(hoverAnnId);
      } else if (e.key === "ArrowRight") gotoSibling(1);
      else if (e.key === "ArrowLeft") gotoSibling(-1);
    }
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool, polyPoints, hoverAnnId, siblings, imageId, undoStack, redoStack]);

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

  // --- Auto-tool: multi-prompt predict whenever prompts change ---
  const promptsKey = JSON.stringify(prompts);
  useEffect(() => {
    if (tool !== "auto") return;
    if (prompts.points.length === 0 && !prompts.box) {
      setPending(null);
      return;
    }
    if (!image || image.status !== "ready") return;
    if (!activeClassId) return;
    let cancelled = false;
    (async () => {
      setPredicting(true); setError(null);
      try {
        const r = await api.predict(imageId, {
          points: prompts.points.length ? prompts.points.map(([x, y]) => [x, y] as [number, number]) : undefined,
          point_labels: prompts.points.length ? prompts.points.map(([, , l]) => l) : undefined,
          box: prompts.box ? [
            prompts.box[0], prompts.box[1],
            prompts.box[0] + prompts.box[2], prompts.box[1] + prompts.box[3],
          ] : undefined,
          ...(predictModelId !== undefined ? { model_version_id: predictModelId } : {}),
        });
        if (!cancelled) setPending({ polygon: r.polygon, score: r.score, bbox: r.bbox });
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "predict failed");
      } finally {
        if (!cancelled) setPredicting(false);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptsKey, tool, imageId, image?.status, activeClassId, predictModelId]);

  function clearPrompts() {
    setPrompts({ points: [], box: null });
    setPending(null);
    setAutoBoxPreview(null);
    dragStartRef.current = null;
    wasDragRef.current = false;
  }

  // Click
  async function onStageClick(e: Konva.KonvaEventObject<MouseEvent>) {
    // Suppress click that came from a drag (auto box / vertex edit)
    if (wasDragRef.current) { wasDragRef.current = false; return; }
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
      const evt = e.evt;
      const additive = evt.shiftKey || evt.altKey || evt.metaKey;
      const label: 0 | 1 = evt.altKey ? 0 : 1;
      if (additive && (prompts.points.length > 0 || prompts.box)) {
        setPrompts((p) => ({ ...p, points: [...p.points, [ix, iy, label]] }));
      } else {
        setPrompts({ points: [[ix, iy, 1]], box: null });
      }
    } else if (tool === "polygon") {
      setPolyPoints([...polyPoints, [ix, iy]]);
    }
  }

  // Mouse down/move/up — handles bbox tool AND auto-tool drag-to-box
  function onMouseDown(_e: Konva.KonvaEventObject<MouseEvent>) {
    if (tool === "bbox") {
      const stage = stageRef.current; if (!stage) return;
      const p = stage.getPointerPosition(); if (!p) return;
      setBboxStart(imagePoint(p.x, p.y));
      setBboxEnd(imagePoint(p.x, p.y));
      return;
    }
    if (tool === "auto") {
      const stage = stageRef.current; if (!stage) return;
      const p = stage.getPointerPosition(); if (!p) return;
      dragStartRef.current = imagePoint(p.x, p.y);
      wasDragRef.current = false;
      setAutoBoxPreview(null);
    }
  }
  function onMouseMove() {
    const stage = stageRef.current; if (!stage) return;
    const p = stage.getPointerPosition(); if (!p) return;
    if (tool === "bbox" && bboxStart) {
      setBboxEnd(imagePoint(p.x, p.y));
      return;
    }
    if (tool === "auto" && dragStartRef.current) {
      const [ix, iy] = imagePoint(p.x, p.y);
      const [sx, sy] = dragStartRef.current;
      const dx = Math.abs(ix - sx), dy = Math.abs(iy - sy);
      if (dx > 4 || dy > 4) {
        wasDragRef.current = true;
        setAutoBoxPreview([Math.min(sx, ix), Math.min(sy, iy), dx, dy]);
      }
    }
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
        pushOp({ kind: "create", ann: a });
      } catch (err) { setError(err instanceof Error ? err.message : "save failed"); }
      return;
    }
    if (tool === "auto") {
      if (wasDragRef.current && autoBoxPreview) {
        // Drag-box prompt → REPLACES existing prompts
        if (autoBoxPreview[2] >= 4 && autoBoxPreview[3] >= 4) {
          setPrompts({ points: [], box: autoBoxPreview });
        }
        setAutoBoxPreview(null);
      }
      dragStartRef.current = null;
      // wasDragRef cleared by the click handler that fires next
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
      pushOp({ kind: "create", ann: a });
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
      pushOp({ kind: "create", ann: a });
      clearPrompts();
    } catch (err) { setError(err instanceof Error ? err.message : "save failed"); }
  }

  async function deleteAnnotation(id: string) {
    const target = annotations.find((a) => a.id === id);
    await api.deleteAnnotation(id);
    setAnnotations((arr) => arr.filter((a) => a.id !== id));
    if (selectedAnnId === id) { setSelectedAnnId(null); setEditPoly(null); }
    if (target) pushOp({ kind: "delete", ann: target });
  }

  // ---- Polygon vertex editing ----
  function selectAnnotation(a: Annotation) {
    if (a.geometry_type !== "polygon" || !a.geometry.points) {
      setSelectedAnnId(a.id); setEditPoly(null); editBeforeRef.current = null; return;
    }
    setSelectedAnnId(a.id);
    setEditPoly(a.geometry.points.map((p: number[]) => [p[0], p[1]] as [number, number]));
    editBeforeRef.current = a;
  }

  async function flushEditPoly() {
    if (!selectedAnnId || !editPoly) return;
    const before = editBeforeRef.current;
    try {
      const updated = await api.updateAnnotation(selectedAnnId, {
        geometry: { points: editPoly },
      });
      setAnnotations((arr) => arr.map((x) => (x.id === selectedAnnId ? updated : x)));
      if (before && before.id === updated.id) {
        // Skip no-op (geometry unchanged)
        const samePts = JSON.stringify(before.geometry?.points) === JSON.stringify(updated.geometry?.points);
        if (!samePts) pushOp({ kind: "update", before, after: updated });
      }
      editBeforeRef.current = updated;
    } catch (err) {
      setError(err instanceof Error ? err.message : "update failed");
    }
  }

  function moveVertex(i: number, x: number, y: number) {
    if (!editPoly) return;
    const next = editPoly.slice();
    next[i] = [x, y];
    setEditPoly(next);
  }

  function deleteVertex(i: number) {
    if (!editPoly || editPoly.length <= 3) {
      setError("Polygon needs at least 3 vertices");
      return;
    }
    const next = editPoly.filter((_, idx) => idx !== i);
    setEditPoly(next);
    setTimeout(flushEditPoly, 0);
  }

  function insertVertexAt(afterIndex: number) {
    if (!editPoly) return;
    const i = afterIndex;
    const j = (i + 1) % editPoly.length;
    const mx = (editPoly[i][0] + editPoly[j][0]) / 2;
    const my = (editPoly[i][1] + editPoly[j][1]) / 2;
    const next = [...editPoly.slice(0, j), [mx, my] as [number, number], ...editPoly.slice(j)];
    setEditPoly(next);
    setTimeout(flushEditPoly, 0);
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
        <Link to={`/projects/${projectId}/datasets/${datasetId}`} className="text-neutral-400 hover:text-neutral-100">
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
        <div className="flex items-center gap-1">
          <button
            onClick={undo}
            disabled={undoStack.length === 0}
            title={`Undo (⌘Z) — ${undoStack.length} step${undoStack.length === 1 ? "" : "s"} available`}
            className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40 disabled:hover:bg-neutral-800"
          >
            ↶ Undo
          </button>
          <button
            onClick={redo}
            disabled={redoStack.length === 0}
            title={`Redo (⌘⇧Z) — ${redoStack.length} step${redoStack.length === 1 ? "" : "s"} available`}
            className="text-xs px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 disabled:opacity-40 disabled:hover:bg-neutral-800"
          >
            ↷ Redo
          </button>
        </div>
        <ModelPicker
          models={models}
          activeModelId={activeModelId}
          choice={modelChoice}
          onChange={setModelChoice}
        />
        <button
          onClick={gotoMostUncertain}
          disabled={findingNext}
          title="Jump to the un-annotated image the active model is least sure about"
          className="text-xs bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 font-medium px-3 py-1 rounded"
        >
          {findingNext ? "Finding…" : "Next uncertain ✨"}
        </button>
        <div className="text-xs text-neutral-400">{idx + 1} / {siblings.length}</div>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left toolbar */}
        <div className="w-14 bg-neutral-900 border-r border-neutral-800 flex flex-col items-center py-3 gap-2">
          <ToolBtn active={tool === "pan"} onClick={() => setTool("pan")} label="Pan (V)" icon="✋" />
          <ToolBtn active={tool === "auto"} onClick={() => setTool("auto")} label="Auto-Annotate (A) — click to segment, shift-click +pt, alt-click −pt, drag for box" icon="✨" />
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
                const isSelected = selectedAnnId === a.id;
                if (a.geometry_type === "polygon" && a.geometry.points) {
                  // when selected, render the local edit buffer instead of saved geometry
                  const pts = isSelected && editPoly ? editPoly : a.geometry.points;
                  const flat = pts.flat();
                  return (
                    <Line
                      key={a.id}
                      points={flat}
                      closed
                      fill={color + (isSelected ? "55" : "33")}
                      stroke={color}
                      strokeWidth={(isSelected ? 3 : 2) / view.scale}
                      onClick={(ev) => { ev.cancelBubble = true; selectAnnotation(a); }}
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
                      strokeWidth={(isSelected ? 3 : 2) / view.scale}
                      fill={color + "22"}
                      onClick={(ev) => { ev.cancelBubble = true; selectAnnotation(a); }}
                      onMouseEnter={() => setHoverAnnId(a.id)}
                      onMouseLeave={() => setHoverAnnId(null)}
                    />
                  );
                }
                return null;
              })}

              {/* Vertex + edge-midpoint handles for selected polygon */}
              {selectedAnnId && editPoly && (() => {
                const sel = annotations.find((x) => x.id === selectedAnnId);
                const color = sel ? (classMap.get(sel.class_id)?.color ?? "#3b82f6") : "#3b82f6";
                const r = 5 / view.scale;
                const er = 3.5 / view.scale;
                return (
                  <>
                    {editPoly.map(([x, y], i) => {
                      const j = (i + 1) % editPoly.length;
                      const mx = (x + editPoly[j][0]) / 2;
                      const my = (y + editPoly[j][1]) / 2;
                      return (
                        <Circle
                          key={`mid-${i}`}
                          x={mx} y={my} radius={er}
                          fill="#ffffff" stroke={color}
                          strokeWidth={1 / view.scale}
                          opacity={0.8}
                          onClick={(ev) => { ev.cancelBubble = true; wasDragRef.current = true; insertVertexAt(i); }}
                        />
                      );
                    })}
                    {editPoly.map(([x, y], i) => (
                      <Circle
                        key={`v-${i}`}
                        x={x} y={y} radius={r}
                        fill={color} stroke="#ffffff"
                        strokeWidth={1.5 / view.scale}
                        draggable
                        onClick={(ev) => {
                          if (ev.evt.shiftKey || ev.evt.altKey) {
                            ev.cancelBubble = true; wasDragRef.current = true; deleteVertex(i);
                          }
                        }}
                        onDragMove={(ev) => moveVertex(i, ev.target.x(), ev.target.y())}
                        onDragEnd={() => { wasDragRef.current = true; flushEditPoly(); }}
                      />
                    ))}
                  </>
                );
              })()}
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
              {/* SAM2 prompt markers */}
              {prompts.points.map(([x, y, label], i) => (
                <Circle
                  key={`pp-${i}`}
                  x={x} y={y} radius={6 / view.scale}
                  fill={label === 1 ? "#22c55e" : "#ef4444"}
                  stroke="#ffffff" strokeWidth={1.5 / view.scale}
                />
              ))}
              {prompts.box && (
                <Rect
                  x={prompts.box[0]} y={prompts.box[1]}
                  width={prompts.box[2]} height={prompts.box[3]}
                  stroke="#3b82f6" strokeWidth={2 / view.scale}
                  dash={[8 / view.scale, 4 / view.scale]}
                />
              )}
              {autoBoxPreview && (
                <Rect
                  x={autoBoxPreview[0]} y={autoBoxPreview[1]}
                  width={autoBoxPreview[2]} height={autoBoxPreview[3]}
                  stroke="#3b82f6" strokeWidth={1.5 / view.scale}
                  dash={[4 / view.scale, 4 / view.scale]} opacity={0.7}
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
              <button onClick={() => setPending(null)} className="text-neutral-400 px-2 hover:text-neutral-100">
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
              className="text-neutral-400 hover:text-neutral-100 text-lg px-2"
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
              className="text-neutral-400 hover:text-neutral-100 text-lg px-2"
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
                      activeClassId === c.id ? "bg-neutral-800 text-neutral-100" : "text-neutral-400 hover:bg-neutral-800/50"
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
        active ? "bg-emerald-500 text-neutral-950" : "text-neutral-400 hover:bg-neutral-800 hover:text-neutral-100"
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
