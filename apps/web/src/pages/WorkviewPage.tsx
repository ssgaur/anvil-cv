import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Circle, Image as KImage, Layer, Line, Rect, Stage } from "react-konva";
import Konva from "konva";
import { extractLargestPolygon } from "../lib/brush";
import { api, fileUrl } from "../lib/api";
import { toast } from "../lib/toast";
import { useAuth } from "../store/auth";
import { isLabeler, isReviewer } from "../lib/roles";
import { RejectReasonDialog } from "../components/RejectReasonDialog";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { Annotation, ClassProperty, ClassRow, CommentRow, ImageRow, ModelVersion } from "../types";

type Tool = "pan" | "auto" | "polygon" | "polyline" | "bbox" | "obb" | "brush" | "keypoint" | "comment";

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
  // Polyline (open) accumulates points like polygon; commit on Enter or dbl-click.
  const [polylinePoints, setPolylinePoints] = useState<[number, number][]>([]);
  // Keypoint creation: building an array of [x,y,v] per the active class's schema.
  const [kpDrops, setKpDrops] = useState<[number, number, 0 | 1 | 2][]>([]);
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
  const [editPolyline, setEditPolyline] = useState<[number, number][] | null>(null);
  // Bbox handle edit (live buffer while dragging handles or moving the box)
  const [editBbox, setEditBbox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [editObb, setEditObb] = useState<{ cx: number; cy: number; w: number; h: number; angle_rad: number } | null>(null);
  const [editKeypoints, setEditKeypoints] = useState<[number, number, 0 | 1 | 2][] | null>(null);
  const editPolylineRef = useRef<[number, number][] | null>(null);
  const editObbRef = useRef<{ cx: number; cy: number; w: number; h: number; angle_rad: number } | null>(null);
  const editKeypointsRef = useRef<[number, number, 0 | 1 | 2][] | null>(null);
  // Snapshot of the annotation taken when entering edit mode (for undo of polygon edits)
  const editBeforeRef = useRef<Annotation | null>(null);

  // Cache of class properties per class id, lazy-loaded when an annotation is selected
  const [classPropsCache, setClassPropsCache] = useState<Record<string, ClassProperty[]>>({});

  // Review-flow comments
  const [comments, setComments] = useState<CommentRow[]>([]);
  const [hoveredCommentId, setHoveredCommentId] = useState<string | null>(null);
  // Pending pin (clicked with comment tool, awaiting body text)
  const [pendingPin, setPendingPin] = useState<{ x: number; y: number } | null>(null);
  const [pendingBody, setPendingBody] = useState("");

  // ---- Brush tool
  const maskCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const [maskVersion, setMaskVersion] = useState(0); // bump to force Konva re-render
  const [hasBrushStrokes, setHasBrushStrokes] = useState(false);
  const [brushSize, setBrushSize] = useState(24);    // image-space radius (px)
  const [brushPreview, setBrushPreview] = useState<{ pt: [number, number]; erase: boolean } | null>(null);
  const brushingRef = useRef<{ erase: boolean; lastPt: [number, number] | null } | null>(null);

  function ensureMaskCanvas(): HTMLCanvasElement | null {
    if (!image) return null;
    const cur = maskCanvasRef.current;
    if (!cur || cur.width !== image.width || cur.height !== image.height) {
      const c = document.createElement("canvas");
      c.width = image.width;
      c.height = image.height;
      maskCanvasRef.current = c;
    }
    return maskCanvasRef.current;
  }

  function clearMask() {
    const c = maskCanvasRef.current;
    if (c) {
      const ctx = c.getContext("2d");
      ctx?.clearRect(0, 0, c.width, c.height);
    }
    setHasBrushStrokes(false);
    setMaskVersion((v) => v + 1);
  }

  function clearEditBuffers() {
    setEditPoly(null);
    editPolylineRef.current = null;
    setEditPolyline(null);
    setEditBbox(null);
    editObbRef.current = null;
    setEditObb(null);
    editKeypointsRef.current = null;
    setEditKeypoints(null);
    editBeforeRef.current = null;
  }

  function setEditPolylineLive(next: [number, number][] | null) {
    editPolylineRef.current = next;
    setEditPolyline(next);
  }

  function setEditObbLive(next: { cx: number; cy: number; w: number; h: number; angle_rad: number } | null) {
    editObbRef.current = next;
    setEditObb(next);
  }

  function setEditKeypointsLive(next: [number, number, 0 | 1 | 2][] | null) {
    editKeypointsRef.current = next;
    setEditKeypoints(next);
  }

  function paintStroke(from: [number, number] | null, to: [number, number], erase: boolean) {
    const c = ensureMaskCanvas();
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const cls = activeClassId ? classMap.get(activeClassId) : null;
    const color = cls?.color ?? "#22c55e";
    ctx.globalCompositeOperation = erase ? "destination-out" : "source-over";
    ctx.fillStyle = color;
    ctx.strokeStyle = color;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = brushSize * 2;
    if (from) {
      ctx.beginPath();
      ctx.moveTo(from[0], from[1]);
      ctx.lineTo(to[0], to[1]);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(to[0], to[1], brushSize, 0, Math.PI * 2);
    ctx.fill();
    setHasBrushStrokes(true);
    setMaskVersion((v) => v + 1);
  }

  function updateBrushPreview(pos: { x: number; y: number } | null, erase: boolean) {
    if (!image || !pos) {
      setBrushPreview(null);
      return;
    }
    const pt = imagePoint(pos.x, pos.y);
    if (pt[0] < 0 || pt[1] < 0 || pt[0] > image.width || pt[1] > image.height) {
      setBrushPreview(null);
      return;
    }
    setBrushPreview({ pt, erase });
  }

  async function commitBrush() {
    if (!image || !activeClassId || !maskCanvasRef.current) return;
    const poly = extractLargestPolygon(maskCanvasRef.current, 32, 1.5);
    if (poly.length < 3) {
      setError("Brushed area too small to extract a polygon");
      return;
    }
    try {
      const a = await api.createAnnotation(imageId, {
        class_id: activeClassId,
        geometry_type: "polygon",
        geometry: { points: poly },
        source: "manual",
      });
      setAnnotations((arr) => [...arr, a]);
      pushOp({ kind: "create", ann: a });
      clearMask();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    }
  }

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
    if (selectedAnnId === op.ann.id) { setSelectedAnnId(null); clearEditBuffers(); }
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
    if (selectedAnnId === reverted.id) selectAnnotation(reverted);
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
  // Hold-space-to-pan (overrides any active tool while held)
  const [spacePan, setSpacePan] = useState(false);
  const isEditingAnnotation = selectedAnnId !== null;
  const isPanning = spacePan || (tool === "pan" && !isEditingAnnotation);
  // Cheatsheet popover
  const [showHelp, setShowHelp] = useState(false);

  // ---- Data load
  const loadAll = useCallback(async () => {
    const [img, cs, ann, cmts] = await Promise.all([
      api.getImage(imageId),
      api.listClasses(projectId),
      api.listAnnotations(imageId),
      api.listComments(imageId),
    ]);
    setImage(img); setClasses(cs); setAnnotations(ann); setComments(cmts);
    if (cs.length && !activeClassId) setActiveClassId(cs[0].id);
  }, [imageId, projectId, activeClassId]);

  useEffect(() => { loadAll(); /* eslint-disable-next-line */ }, [imageId]);

  // Reset selection / prompts when switching images
  useEffect(() => {
    setSelectedAnnId(null); clearEditBuffers();
    editBeforeRef.current = null;
    setUndoStack([]); setRedoStack([]);
    clearPrompts();
    setPolyPoints([]); setBboxStart(null); setBboxEnd(null);
    setPendingPin(null); setPendingBody("");
    // Drop the brush canvas so the next image starts fresh.
    maskCanvasRef.current = null;
    setBrushPreview(null);
    setHasBrushStrokes(false);
    setMaskVersion((v) => v + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageId]);

  useEffect(() => {
    if (tool !== "brush") setBrushPreview(null);
  }, [tool]);

  // Sibling images (for nav strip)
  useEffect(() => {
    api.listImages(projectId, datasetId, { limit: 500 }).then(setSiblings);
  }, [projectId, datasetId]);

  // Lazy-load class properties when an annotation is selected (cached by class_id)
  const selectedAnn = useMemo(
    () => annotations.find((a) => a.id === selectedAnnId) ?? null,
    [annotations, selectedAnnId],
  );
  useEffect(() => {
    const cid = selectedAnn?.class_id;
    if (!cid || classPropsCache[cid]) return;
    api.listClassProperties(projectId, cid)
      .then((props) => setClassPropsCache((m) => ({ ...m, [cid]: props })))
      .catch(() => {/* swallow — panel will just show empty */});
  }, [selectedAnn, projectId, classPropsCache]);

  async function setAnnotationProperty(annId: string, propId: string, value: string | string[] | null) {
    const ann = annotations.find((a) => a.id === annId);
    if (!ann) return;
    const next = { ...(ann.properties ?? {}) };
    if (value === null || value === "" || (Array.isArray(value) && value.length === 0)) {
      delete next[propId];
    } else {
      next[propId] = value;
    }
    // optimistic
    setAnnotations((arr) => arr.map((a) => a.id === annId ? { ...a, properties: next } : a));
    try {
      const updated = await api.updateAnnotation(annId, { properties: next });
      setAnnotations((arr) => arr.map((a) => a.id === annId ? updated : a));
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update property");
      // rollback by reloading
      api.listAnnotations(imageId).then(setAnnotations);
    }
  }


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
    fitToScreen();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [htmlImage, stageSize]);

  function fitToScreen() {
    if (!htmlImage || !stageSize.w || !stageSize.h) return;
    const scale = Math.min(stageSize.w / htmlImage.width, stageSize.h / htmlImage.height) * 0.95;
    setView({
      scale,
      x: (stageSize.w - htmlImage.width * scale) / 2,
      y: (stageSize.h - htmlImage.height * scale) / 2,
    });
  }

  function zoomTo100() {
    if (!htmlImage || !stageSize.w || !stageSize.h) return;
    setView({
      scale: 1,
      x: (stageSize.w - htmlImage.width) / 2,
      y: (stageSize.h - htmlImage.height) / 2,
    });
  }

  // Per-image timer
  useEffect(() => {
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - imgStartTime) / 1000)), 1000);
    return () => clearInterval(t);
  }, [imgStartTime]);

  // Keyboard shortcuts
  useEffect(() => {
    function k(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const key = e.key.toLowerCase();
      const mod = e.metaKey || e.ctrlKey;
      if (mod && (e.key === "z" || e.key === "Z")) {
        e.preventDefault();
        if (e.shiftKey) redo(); else undo();
        return;
      }
      if (mod && (e.key === "y" || e.key === "Y")) { e.preventDefault(); redo(); return; }
      if (e.key === " " || e.code === "Space") {
        // Hold-to-pan; ignore key auto-repeat
        if (!e.repeat) { e.preventDefault(); setSpacePan(true); }
        return;
      }
      if (key === "f") { fitToScreen(); return; }
      if (e.key === "0") { zoomTo100(); return; }
      if (e.key === "?" || (e.key === "/" && e.shiftKey)) { e.preventDefault(); setShowHelp((v) => !v); return; }
      if (key === "h") { setShowHelp((v) => !v); return; }
      if (key === "v") activateTool("pan");
      else if (key === "a") activateTool("auto");
      else if (key === "p") activateTool("polygon");
      else if (key === "l") activateTool("polyline");
      else if (key === "b") activateTool("bbox");
      else if (key === "o") activateTool("obb");
      else if (key === "r") activateTool("brush");
      else if (key === "k") activateTool("keypoint");
      else if (key === "t") activateTool("comment");
      else if (e.key === "Escape") {
        if (showHelp) { setShowHelp(false); return; }
        clearCreationState();
        setSelectedAnnId(null); clearEditBuffers();
      }
      else if (e.key === "Enter" && tool === "polygon" && polyPoints.length >= 3) commitPolygon();
      else if (e.key === "Enter" && tool === "polyline" && polylinePoints.length >= 2) commitPolyline();
      else if (e.key === "Enter" && tool === "brush" && hasBrushStrokes) commitBrush();
      else if (e.key === "[" && tool === "brush") setBrushSize((s) => Math.max(2, Math.round(s * 0.8)));
      else if (e.key === "]" && tool === "brush") setBrushSize((s) => Math.min(200, Math.round(s * 1.25)));
      else if (e.key === "Delete" || e.key === "Backspace") {
        if (hoverAnnId) deleteAnnotation(hoverAnnId);
      } else if (e.key === "ArrowRight") gotoSibling(1);
      else if (e.key === "ArrowLeft") gotoSibling(-1);
      else if (e.key === "+" || e.key === "=") { zoomBy(1.25); }
      else if (e.key === "-" || e.key === "_") { zoomBy(0.8); }
      else if (/^[1-9]$/.test(e.key)) {
        const idx = parseInt(e.key, 10) - 1;
        const c = classes[idx];
        if (c) setActiveClassId(c.id);
      }
    }
    function ku(e: KeyboardEvent) {
      if (e.key === " " || e.code === "Space") setSpacePan(false);
    }
    function blur() { setSpacePan(false); }
    window.addEventListener("keydown", k);
    window.addEventListener("keyup", ku);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", k);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("blur", blur);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool, polyPoints, polylinePoints, activeClassId, image, hoverAnnId, siblings, imageId, undoStack, redoStack, hasBrushStrokes, showHelp, classes, isEditingAnnotation]);

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

  // Centered zoom by factor (used by +/- shortcuts).
  function zoomBy(factor: number) {
    const oldScale = view.scale;
    const newScale = Math.max(0.05, Math.min(20, oldScale * factor));
    const cx = stageSize.w / 2;
    const cy = stageSize.h / 2;
    const mx = (cx - view.x) / oldScale;
    const my = (cy - view.y) / oldScale;
    setView({ scale: newScale, x: cx - mx * newScale, y: cy - my * newScale });
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

  function clearCreationState() {
    setPolyPoints([]);
    setPolylinePoints([]);
    setKpDrops([]);
    setBboxStart(null);
    setBboxEnd(null);
    setPendingPin(null);
    setPendingBody("");
    brushingRef.current = null;
    setBrushPreview(null);
    clearPrompts();
    clearMask();
  }

  function activateTool(next: Tool) {
    if (next !== tool || isEditingAnnotation) {
      clearCreationState();
    }
    setSelectedAnnId(null);
    clearEditBuffers();
    setTool(next);
  }

  // Click
  async function onStageClick(e: Konva.KonvaEventObject<MouseEvent>) {
    // Don't treat pan-clicks as tool-clicks
    if (isPanning || e.evt.button === 1) return;
    // Suppress click that came from a drag (auto box / vertex edit)
    if (wasDragRef.current) { wasDragRef.current = false; return; }
    if (isEditingAnnotation) {
      setSelectedAnnId(null);
      clearEditBuffers();
      return;
    }
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
      if (!activeClassId) { setError("Pick a class on the right first."); return; }
      setPolyPoints([...polyPoints, [ix, iy]]);
    } else if (tool === "polyline") {
      if (!activeClassId) { setError("Pick a class on the right first."); return; }
      setPolylinePoints([...polylinePoints, [ix, iy]]);
    } else if (tool === "keypoint") {
      const cls = classes.find((c) => c.id === activeClassId);
      if (!cls) { setError("Pick a class first."); return; }
      if (cls.geometry_type !== "keypoints" || !cls.keypoint_schema) {
        setError("Active class must be a keypoints class with a schema.");
        return;
      }
      const total = cls.keypoint_schema.keypoints.length;
      if (kpDrops.length >= total) return;
      // Cmd/Ctrl-click = visibility 1 (occluded but labeled); plain click = 2 (visible).
      const v: 1 | 2 = (e.evt.metaKey || e.evt.ctrlKey) ? 1 : 2;
      const next: [number, number, 0 | 1 | 2][] = [...kpDrops, [Math.round(ix), Math.round(iy), v]];
      setKpDrops(next);
      if (next.length === total) {
        commitKeypoints(next);
      }
    } else if (tool === "comment") {
      setPendingPin({ x: Math.round(ix), y: Math.round(iy) });
      setPendingBody("");
    }
  }

  // Mouse down/move/up — handles bbox tool AND auto-tool drag-to-box
  function onMouseDown(e: Konva.KonvaEventObject<MouseEvent>) {
    // Pan modes (hold-space, pan tool, middle-mouse) bypass tool handlers
    // — Konva's own draggable={isPanning || middle} handles the drag.
    if (isPanning || e.evt.button === 1) return;
    if (isEditingAnnotation) return;
    if (tool === "brush") {
      const stage = stageRef.current; if (!stage) return;
      const p = stage.getPointerPosition(); if (!p) return;
      const pt = imagePoint(p.x, p.y);
      const erase = e.evt.shiftKey || e.evt.altKey;
      updateBrushPreview(p, erase);
      brushingRef.current = { erase, lastPt: pt };
      paintStroke(null, pt, erase);
      return;
    }
    if (tool === "bbox" || tool === "obb") {
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
  function onMouseMove(e: Konva.KonvaEventObject<MouseEvent>) {
    const stage = stageRef.current; if (!stage) return;
    const p = stage.getPointerPosition(); if (!p) return;
    if (isEditingAnnotation) return;
    if (tool === "brush") {
      const erase = Boolean(brushingRef.current?.erase || e.evt.shiftKey || e.evt.altKey);
      updateBrushPreview(p, erase);
    }
    if (tool === "brush" && brushingRef.current) {
      const pt = imagePoint(p.x, p.y);
      const { erase, lastPt } = brushingRef.current;
      paintStroke(lastPt, pt, erase);
      brushingRef.current = { erase, lastPt: pt };
      return;
    }
    if ((tool === "bbox" || tool === "obb") && bboxStart) {
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
    if (isEditingAnnotation) return;
    if (tool === "brush") {
      brushingRef.current = null;
      return;
    }
    if ((tool === "bbox" || tool === "obb") && bboxStart && bboxEnd && image && activeClassId) {
      const x = Math.min(bboxStart[0], bboxEnd[0]);
      const y = Math.min(bboxStart[1], bboxEnd[1]);
      const w = Math.abs(bboxEnd[0] - bboxStart[0]);
      const h = Math.abs(bboxEnd[1] - bboxStart[1]);
      const isObb = tool === "obb";
      setBboxStart(null); setBboxEnd(null);
      if (w < 4 || h < 4) return;
      try {
        const a = isObb
          ? await api.createAnnotation(imageId, {
              class_id: activeClassId,
              geometry_type: "obb",
              geometry: { cx: x + w / 2, cy: y + h / 2, w, h, angle_rad: 0 },
              source: "manual",
            })
          : await api.createAnnotation(imageId, {
              class_id: activeClassId,
              geometry_type: "bbox",
              geometry: { x, y, w, h },
              source: "manual",
            });
        setAnnotations((arr) => [...arr, a]);
        pushOp({ kind: "create", ann: a });
        if (isObb) {
          selectAnnotation(a);
        }
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
    if (!image) return;
    if (!activeClassId) { setError("Pick a class on the right first."); return; }
    if (polyPoints.length < 3) { setError("Polygon needs at least 3 points."); return; }
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

  async function commitPolyline() {
    if (!image) return;
    if (!activeClassId) { setError("Pick a class on the right first."); return; }
    if (polylinePoints.length < 2) { setError("Polyline needs at least 2 points."); return; }
    try {
      const a = await api.createAnnotation(imageId, {
        class_id: activeClassId,
        geometry_type: "polyline",
        geometry: { points: polylinePoints },
        source: "manual",
      });
      setAnnotations((arr) => [...arr, a]);
      pushOp({ kind: "create", ann: a });
      setPolylinePoints([]);
    } catch (err) { setError(err instanceof Error ? err.message : "save failed"); }
  }

  async function commitKeypoints(pts: [number, number, 0 | 1 | 2][]) {
    if (!image || !activeClassId) return;
    try {
      const a = await api.createAnnotation(imageId, {
        class_id: activeClassId,
        geometry_type: "keypoints",
        geometry: { points: pts },
        source: "manual",
      });
      setAnnotations((arr) => [...arr, a]);
      pushOp({ kind: "create", ann: a });
      setKpDrops([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
      setKpDrops([]);
    }
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
    if (selectedAnnId === id) { setSelectedAnnId(null); clearEditBuffers(); }
    if (target) pushOp({ kind: "delete", ann: target });
  }

  // ---- Polygon vertex editing ----
  function selectAnnotation(a: Annotation) {
    clearCreationState();
    setTool("pan");
    setSelectedAnnId(a.id);
    clearEditBuffers();
    if (a.geometry_type === "polygon" && a.geometry.points) {
      setEditPoly(a.geometry.points.map((p: number[]) => [p[0], p[1]] as [number, number]));
      editBeforeRef.current = a;
      return;
    }
    if (a.geometry_type === "polyline" && a.geometry.points) {
      setEditPolylineLive(a.geometry.points.map((p: number[]) => [p[0], p[1]] as [number, number]));
      editBeforeRef.current = a;
      return;
    }
    if (a.geometry_type === "bbox" && a.geometry.x !== undefined) {
      setEditBbox({ x: a.geometry.x!, y: a.geometry.y!, w: a.geometry.w!, h: a.geometry.h! });
      editBeforeRef.current = a;
      return;
    }
    if (a.geometry_type === "obb" && a.geometry.cx !== undefined) {
      setEditObbLive({
        cx: a.geometry.cx!,
        cy: a.geometry.cy!,
        w: a.geometry.w!,
        h: a.geometry.h!,
        angle_rad: a.geometry.angle_rad ?? 0,
      });
      editBeforeRef.current = a;
      return;
    }
    if (a.geometry_type === "keypoints" && a.geometry.points) {
      setEditKeypointsLive(a.geometry.points.map((p: number[]) => [p[0], p[1], (p[2] ?? 2) as 0 | 1 | 2]));
      editBeforeRef.current = a;
    }
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

  async function flushEditPolyline(points = editPolylineRef.current) {
    if (!selectedAnnId || !points) return;
    const before = editBeforeRef.current;
    try {
      const updated = await api.updateAnnotation(selectedAnnId, {
        geometry: { points },
      });
      setAnnotations((arr) => arr.map((x) => (x.id === selectedAnnId ? updated : x)));
      if (before && before.id === updated.id) {
        const samePts = JSON.stringify(before.geometry?.points) === JSON.stringify(updated.geometry?.points);
        if (!samePts) pushOp({ kind: "update", before, after: updated });
      }
      editBeforeRef.current = updated;
    } catch (err) {
      setError(err instanceof Error ? err.message : "update failed");
    }
  }

  function movePolylineVertex(i: number, x: number, y: number) {
    const cur = editPolylineRef.current ?? editPolyline;
    if (!cur) return;
    const next = cur.slice();
    next[i] = [x, y];
    setEditPolylineLive(next);
  }

  function deletePolylineVertex(i: number) {
    const cur = editPolylineRef.current ?? editPolyline;
    if (!cur || cur.length <= 2) {
      setError("Polyline needs at least 2 vertices");
      return;
    }
    const next = cur.filter((_, idx) => idx !== i);
    setEditPolylineLive(next);
    flushEditPolyline(next);
  }

  function insertPolylineVertexAt(afterIndex: number) {
    const cur = editPolylineRef.current ?? editPolyline;
    if (!cur) return;
    const i = afterIndex;
    const j = i + 1;
    if (!cur[j]) return;
    const mx = (cur[i][0] + cur[j][0]) / 2;
    const my = (cur[i][1] + cur[j][1]) / 2;
    const next = [...cur.slice(0, j), [mx, my] as [number, number], ...cur.slice(j)];
    setEditPolylineLive(next);
    flushEditPolyline(next);
  }

  // ---- Bbox 8-handle editing ----
  // Live update of the in-progress bbox while a handle or the body is dragged.
  // Anchor = the corner that should stay fixed (or null for body-drag).
  function resizeBbox(anchor: "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w", x: number, y: number) {
    if (!editBbox || !image) return;
    let { x: bx, y: by, w, h } = editBbox;
    const x2 = bx + w;
    const y2 = by + h;
    let nx1 = bx, ny1 = by, nx2 = x2, ny2 = y2;
    if (anchor.includes("w")) nx1 = x;
    if (anchor.includes("e")) nx2 = x;
    if (anchor.includes("n")) ny1 = y;
    if (anchor.includes("s")) ny2 = y;
    // Clamp to image bounds
    nx1 = Math.max(0, Math.min(image.width, nx1));
    nx2 = Math.max(0, Math.min(image.width, nx2));
    ny1 = Math.max(0, Math.min(image.height, ny1));
    ny2 = Math.max(0, Math.min(image.height, ny2));
    // Normalize so w/h stay positive (handle "flipped" drags through the opposite edge)
    const fx = Math.min(nx1, nx2);
    const fy = Math.min(ny1, ny2);
    const fw = Math.max(1, Math.abs(nx2 - nx1));
    const fh = Math.max(1, Math.abs(ny2 - ny1));
    setEditBbox({ x: fx, y: fy, w: fw, h: fh });
  }

  function moveBbox(dx: number, dy: number) {
    if (!editBbox || !image) return;
    const x = Math.max(0, Math.min(image.width - editBbox.w, editBbox.x + dx));
    const y = Math.max(0, Math.min(image.height - editBbox.h, editBbox.y + dy));
    setEditBbox({ ...editBbox, x, y });
  }

  async function flushEditBbox() {
    if (!selectedAnnId || !editBbox) return;
    const before = editBeforeRef.current;
    try {
      const updated = await api.updateAnnotation(selectedAnnId, {
        geometry: { ...editBbox },
      });
      setAnnotations((arr) => arr.map((x) => (x.id === selectedAnnId ? updated : x)));
      if (before && before.id === updated.id) {
        const same =
          before.geometry?.x === updated.geometry?.x &&
          before.geometry?.y === updated.geometry?.y &&
          before.geometry?.w === updated.geometry?.w &&
          before.geometry?.h === updated.geometry?.h;
        if (!same) pushOp({ kind: "update", before, after: updated });
      }
      editBeforeRef.current = updated;
    } catch (err) {
      setError(err instanceof Error ? err.message : "update failed");
    }
  }

  function rotatePoint(x: number, y: number, cx: number, cy: number, angle: number): [number, number] {
    const c = Math.cos(angle);
    const s = Math.sin(angle);
    const dx = x - cx;
    const dy = y - cy;
    return [cx + dx * c - dy * s, cy + dx * s + dy * c];
  }

  function obbCorners(obb: { cx: number; cy: number; w: number; h: number; angle_rad: number }) {
    const hw = obb.w / 2;
    const hh = obb.h / 2;
    return [
      rotatePoint(obb.cx - hw, obb.cy - hh, obb.cx, obb.cy, obb.angle_rad),
      rotatePoint(obb.cx + hw, obb.cy - hh, obb.cx, obb.cy, obb.angle_rad),
      rotatePoint(obb.cx + hw, obb.cy + hh, obb.cx, obb.cy, obb.angle_rad),
      rotatePoint(obb.cx - hw, obb.cy + hh, obb.cx, obb.cy, obb.angle_rad),
    ] as [number, number][];
  }

  function updateObbSizeFromHandle(x: number, y: number) {
    const cur = editObbRef.current ?? editObb;
    if (!cur) return;
    const c = Math.cos(-cur.angle_rad);
    const s = Math.sin(-cur.angle_rad);
    const dx = x - cur.cx;
    const dy = y - cur.cy;
    const localX = dx * c - dy * s;
    const localY = dx * s + dy * c;
    setEditObbLive({
      ...cur,
      w: Math.max(2, Math.abs(localX) * 2),
      h: Math.max(2, Math.abs(localY) * 2),
    });
  }

  function updateObbRotation(x: number, y: number) {
    const cur = editObbRef.current ?? editObb;
    if (!cur) return;
    setEditObbLive({
      ...cur,
      angle_rad: Math.atan2(y - cur.cy, x - cur.cx) + Math.PI / 2,
    });
  }

  async function flushEditObb(obb = editObbRef.current) {
    if (!selectedAnnId || !obb) return;
    const before = editBeforeRef.current;
    try {
      const updated = await api.updateAnnotation(selectedAnnId, {
        geometry: { ...obb },
      });
      setAnnotations((arr) => arr.map((x) => (x.id === selectedAnnId ? updated : x)));
      if (before && before.id === updated.id) {
        const same = JSON.stringify(before.geometry) === JSON.stringify(updated.geometry);
        if (!same) pushOp({ kind: "update", before, after: updated });
      }
      setEditObbLive({
        cx: updated.geometry.cx!,
        cy: updated.geometry.cy!,
        w: updated.geometry.w!,
        h: updated.geometry.h!,
        angle_rad: updated.geometry.angle_rad ?? 0,
      });
      editBeforeRef.current = updated;
    } catch (err) {
      setError(err instanceof Error ? err.message : "update failed");
    }
  }

  function moveKeypoint(i: number, x: number, y: number) {
    const cur = editKeypointsRef.current ?? editKeypoints;
    if (!cur) return;
    const next = cur.slice();
    next[i] = [x, y, next[i][2]];
    setEditKeypointsLive(next);
  }

  function cycleKeypointVisibility(i: number) {
    const cur = editKeypointsRef.current ?? editKeypoints;
    if (!cur) return;
    const next = cur.slice();
    const v = next[i][2];
    next[i] = [next[i][0], next[i][1], v === 2 ? 1 : v === 1 ? 0 : 2];
    setEditKeypointsLive(next);
    flushEditKeypoints(next);
  }

  async function flushEditKeypoints(points = editKeypointsRef.current) {
    if (!selectedAnnId || !points) return;
    const before = editBeforeRef.current;
    try {
      const updated = await api.updateAnnotation(selectedAnnId, {
        geometry: { points },
      });
      setAnnotations((arr) => arr.map((x) => (x.id === selectedAnnId ? updated : x)));
      if (before && before.id === updated.id) {
        const samePts = JSON.stringify(before.geometry?.points) === JSON.stringify(updated.geometry?.points);
        if (!samePts) pushOp({ kind: "update", before, after: updated });
      }
      setEditKeypointsLive(updated.geometry.points!.map((p: number[]) => [p[0], p[1], (p[2] ?? 2) as 0 | 1 | 2]));
      editBeforeRef.current = updated;
    } catch (err) {
      setError(err instanceof Error ? err.message : "update failed");
    }
  }

  const classMap = useMemo(() => {
    const m = new Map<string, ClassRow>();
    classes.forEach((c) => m.set(c.id, c));
    return m;
  }, [classes]);

  const activeClass = activeClassId ? classMap.get(activeClassId) : null;
  const canvasCursor = isPanning
    ? "grab"
    : isEditingAnnotation
      ? "default"
    : tool === "brush"
        ? "crosshair"
        : tool === "comment"
          ? "copy"
          : "crosshair";

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
        <ReviewControls image={image} onReload={loadAll} />
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
          <ToolBtn active={!isEditingAnnotation && tool === "pan"} onClick={() => activateTool("pan")} label="Pan (V)" icon="✋" />
          <ToolBtn active={!isEditingAnnotation && tool === "auto"} onClick={() => activateTool("auto")} label="Auto-Annotate (A) — click to segment, shift-click +pt, alt-click −pt, drag for box" icon="✨" />
          <ToolBtn active={!isEditingAnnotation && tool === "polygon"} onClick={() => activateTool("polygon")} label="Polygon (P)" icon="⬡" />
          <ToolBtn active={!isEditingAnnotation && tool === "polyline"} onClick={() => activateTool("polyline")} label="Polyline (L) — click to add vertices, Enter to commit (open path)" icon="〰" />
          <ToolBtn active={!isEditingAnnotation && tool === "bbox"} onClick={() => activateTool("bbox")} label="Bounding Box (B)" icon="▭" />
          <ToolBtn active={!isEditingAnnotation && tool === "obb"} onClick={() => activateTool("obb")} label="Rotated Box (O) — drag to place, then use the orange handle to rotate" icon="◇" />
          <ToolBtn active={!isEditingAnnotation && tool === "brush"} onClick={() => activateTool("brush")} label="Brush (R) — paint a mask, shift to erase, [ / ] resize, Enter to commit" icon="🖌" />
          <ToolBtn active={!isEditingAnnotation && tool === "keypoint"} onClick={() => activateTool("keypoint")} label="Keypoint (K) — pick a keypoints class, then click to drop each keypoint in order; Cmd-click = occluded" icon="⊙" />
          <div className="h-px w-8 bg-neutral-800 my-1" />
          <ToolBtn active={!isEditingAnnotation && tool === "comment"} onClick={() => activateTool("comment")} label="Comment (T) — click on image to drop a pin and leave a note for the reviewer" icon="💬" />
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

          {image?.review_status === "rejected" && image.review_reason && (
            <div className="absolute top-3 left-3 bg-rose-900/80 border border-rose-700 text-rose-100 text-xs px-3 py-2 rounded z-10 max-w-md">
              <span className="font-semibold mr-1">Reviewer rejected:</span>{image.review_reason}
            </div>
          )}
          {image?.review_status === "approved" && (
            <div className="absolute top-3 left-3 bg-emerald-900/70 border border-emerald-700 text-emerald-100 text-[11px] px-2.5 py-1 rounded z-10">
              ✓ Approved — read-only. Use Reopen to edit.
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
            onMouseLeave={() => setBrushPreview(null)}
            draggable={isPanning}
            onDragEnd={(e) => {
              if (isPanning) setView({ ...view, x: e.target.x(), y: e.target.y() });
            }}
            x={view.x}
            y={view.y}
            scaleX={view.scale}
            scaleY={view.scale}
            style={{
              cursor: canvasCursor,
              background: "#171717",
            }}
          >
            <Layer>
              {htmlImage && (
                <KImage image={htmlImage} x={0} y={0} width={htmlImage.width} height={htmlImage.height} />
              )}
            </Layer>

            {/* Brush mask overlay (semi-transparent) */}
            {!isEditingAnnotation && tool === "brush" && image && maskCanvasRef.current && (
              <Layer listening={false}>
                <KImage
                  image={maskCanvasRef.current}
                  x={0}
                  y={0}
                  width={image.width}
                  height={image.height}
                  opacity={0.55}
                  // re-render whenever the canvas pixel data changes
                  key={`mask-${maskVersion}`}
                />
              </Layer>
            )}

            {/* Existing annotations */}
            <Layer
              listening
              onMouseDown={(ev) => { ev.cancelBubble = true; }}
              onMouseUp={(ev) => { ev.cancelBubble = true; }}
              onClick={(ev) => { ev.cancelBubble = true; }}
            >
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
                  // While selected, render from the live edit buffer so handle drags update visibly.
                  const bx = isSelected && editBbox ? editBbox.x : a.geometry.x;
                  const by = isSelected && editBbox ? editBbox.y : a.geometry.y!;
                  const bw = isSelected && editBbox ? editBbox.w : a.geometry.w!;
                  const bh = isSelected && editBbox ? editBbox.h : a.geometry.h!;
                  return (
                    <Rect
                      key={a.id}
                      x={bx} y={by}
                      width={bw} height={bh}
                      stroke={color}
                      strokeWidth={(isSelected ? 3 : 2) / view.scale}
                      fill={color + "22"}
                      draggable={isSelected}
                      onDragStart={() => { wasDragRef.current = true; }}
                      onDragMove={(ev) => {
                        if (!isSelected || !editBbox) return;
                        // Convert the Konva node position back into a (dx,dy) relative to the buffer
                        const nx = ev.target.x();
                        const ny = ev.target.y();
                        moveBbox(nx - editBbox.x, ny - editBbox.y);
                      }}
                      onDragEnd={() => { wasDragRef.current = true; flushEditBbox(); }}
                      onClick={(ev) => { ev.cancelBubble = true; selectAnnotation(a); }}
                      onMouseEnter={() => setHoverAnnId(a.id)}
                      onMouseLeave={() => setHoverAnnId(null)}
                    />
                  );
                }
                if (a.geometry_type === "polyline" && a.geometry.points) {
                  const pts = isSelected && editPolyline ? editPolyline : a.geometry.points;
                  const flat = pts.flat();
                  return (
                    <Line
                      key={a.id}
                      points={flat}
                      stroke={color}
                      strokeWidth={(isSelected ? 4 : 2.5) / view.scale}
                      lineCap="round"
                      lineJoin="round"
                      onClick={(ev) => { ev.cancelBubble = true; selectAnnotation(a); }}
                      onMouseEnter={() => setHoverAnnId(a.id)}
                      onMouseLeave={() => setHoverAnnId(null)}
                    />
                  );
                }
                if (a.geometry_type === "obb" && a.geometry.cx !== undefined) {
                  const obb = isSelected && editObb
                    ? editObb
                    : {
                        cx: a.geometry.cx ?? 0,
                        cy: a.geometry.cy ?? 0,
                        w: a.geometry.w ?? 0,
                        h: a.geometry.h ?? 0,
                        angle_rad: a.geometry.angle_rad ?? 0,
                      };
                  // Konva uses degrees; rotation is around (x,y); offset positions
                  // the rect so its center aligns with (cx,cy).
                  return (
                    <Rect
                      key={a.id}
                      x={obb.cx} y={obb.cy}
                      width={obb.w} height={obb.h}
                      offsetX={obb.w / 2} offsetY={obb.h / 2}
                      rotation={(obb.angle_rad * 180) / Math.PI}
                      stroke={color}
                      strokeWidth={(isSelected ? 3 : 2) / view.scale}
                      fill={color + "22"}
                      draggable={isSelected}
                      onDragStart={() => { wasDragRef.current = true; }}
                      onDragMove={(ev) => {
                        if (!isSelected || !editObb) return;
                        setEditObbLive({ ...editObb, cx: ev.target.x(), cy: ev.target.y() });
                      }}
                      onDragEnd={(ev) => {
                        if (editObbRef.current) {
                          setEditObbLive({ ...editObbRef.current, cx: ev.target.x(), cy: ev.target.y() });
                        }
                        wasDragRef.current = true;
                        flushEditObb();
                      }}
                      onClick={(ev) => { ev.cancelBubble = true; selectAnnotation(a); }}
                      onMouseEnter={() => setHoverAnnId(a.id)}
                      onMouseLeave={() => setHoverAnnId(null)}
                    />
                  );
                }
                if (a.geometry_type === "keypoints" && a.geometry.points) {
                  const cls2 = classMap.get(a.class_id);
                  const schema = cls2?.keypoint_schema;
                  const pts = isSelected && editKeypoints ? editKeypoints : a.geometry.points;
                  const r = (isSelected ? 6 : 4) / view.scale;
                  const sw = (isSelected ? 2.5 : 1.5) / view.scale;
                  return (
                    <React.Fragment key={a.id}>
                      {schema?.edges.map(([i, j], k) => {
                        const pi = pts[i];
                        const pj = pts[j];
                        if (!pi || !pj) return null;
                        if (pi[2] === 0 || pj[2] === 0) return null;
                        return (
                          <Line
                            key={`e-${a.id}-${k}`}
                            points={[pi[0], pi[1], pj[0], pj[1]]}
                            stroke={color} strokeWidth={sw}
                            opacity={0.7}
                            onClick={(ev) => { ev.cancelBubble = true; selectAnnotation(a); }}
                          />
                        );
                      })}
                      {pts.map((p, i) => {
                        const v = p[2];
                        if (v === 0) return null;
                        return (
                          <Circle
                            key={`kp-${a.id}-${i}`}
                            x={p[0]} y={p[1]} radius={r}
                            fill={v === 2 ? color : "#000000"}
                            stroke={color} strokeWidth={sw}
                            opacity={v === 2 ? 1 : 0.6}
                            draggable={isSelected}
                            onClick={(ev) => {
                              ev.cancelBubble = true;
                              if (isSelected && (ev.evt.shiftKey || ev.evt.altKey)) {
                                cycleKeypointVisibility(i);
                              } else {
                                selectAnnotation(a);
                              }
                            }}
                            onDragStart={() => { wasDragRef.current = true; }}
                            onDragMove={(ev) => {
                              if (!isSelected) return;
                              moveKeypoint(i, ev.target.x(), ev.target.y());
                            }}
                            onDragEnd={() => { wasDragRef.current = true; flushEditKeypoints(); }}
                            onMouseEnter={() => setHoverAnnId(a.id)}
                            onMouseLeave={() => setHoverAnnId(null)}
                          />
                        );
                      })}
                    </React.Fragment>
                  );
                }
                return null;
              })}

              {/* Vertex + edge-midpoint handles for selected polygon */}
              {selectedAnnId && editPoly && (() => {
                const sel = annotations.find((x) => x.id === selectedAnnId);
                if (!sel || sel.geometry_type !== "polygon") return null;
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

              {/* Vertex + segment-midpoint handles for selected polyline */}
              {selectedAnnId && editPolyline && (() => {
                const sel = annotations.find((x) => x.id === selectedAnnId);
                if (!sel || sel.geometry_type !== "polyline") return null;
                const color = classMap.get(sel.class_id)?.color ?? "#3b82f6";
                const r = 5 / view.scale;
                const er = 3.5 / view.scale;
                return (
                  <>
                    {editPolyline.slice(0, -1).map(([x, y], i) => {
                      const j = i + 1;
                      const mx = (x + editPolyline[j][0]) / 2;
                      const my = (y + editPolyline[j][1]) / 2;
                      return (
                        <Circle
                          key={`pl-mid-${i}`}
                          x={mx} y={my} radius={er}
                          fill="#ffffff" stroke={color}
                          strokeWidth={1 / view.scale}
                          opacity={0.8}
                          onClick={(ev) => { ev.cancelBubble = true; wasDragRef.current = true; insertPolylineVertexAt(i); }}
                        />
                      );
                    })}
                    {editPolyline.map(([x, y], i) => (
                      <Circle
                        key={`pl-v-${i}`}
                        x={x} y={y} radius={r}
                        fill={color} stroke="#ffffff"
                        strokeWidth={1.5 / view.scale}
                        draggable
                        onClick={(ev) => {
                          if (ev.evt.shiftKey || ev.evt.altKey) {
                            ev.cancelBubble = true; wasDragRef.current = true; deletePolylineVertex(i);
                          }
                        }}
                        onDragMove={(ev) => movePolylineVertex(i, ev.target.x(), ev.target.y())}
                        onDragEnd={() => { wasDragRef.current = true; flushEditPolyline(); }}
                      />
                    ))}
                  </>
                );
              })()}

              {/* 8-handle resize for selected bbox */}
              {selectedAnnId && editBbox && (() => {
                const sel = annotations.find((x) => x.id === selectedAnnId);
                if (!sel || sel.geometry_type !== "bbox") return null;
                const color = classMap.get(sel.class_id)?.color ?? "#3b82f6";
                const r = 5 / view.scale;
                const { x, y, w, h } = editBbox;
                const cx = x + w / 2;
                const cy = y + h / 2;
                type Anchor = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
                const handles: { a: Anchor; x: number; y: number; cursor: string }[] = [
                  { a: "nw", x, y, cursor: "nwse-resize" },
                  { a: "n",  x: cx, y, cursor: "ns-resize" },
                  { a: "ne", x: x + w, y, cursor: "nesw-resize" },
                  { a: "e",  x: x + w, y: cy, cursor: "ew-resize" },
                  { a: "se", x: x + w, y: y + h, cursor: "nwse-resize" },
                  { a: "s",  x: cx, y: y + h, cursor: "ns-resize" },
                  { a: "sw", x, y: y + h, cursor: "nesw-resize" },
                  { a: "w",  x, y: cy, cursor: "ew-resize" },
                ];
                return (
                  <>
                    {handles.map((hh) => {
                      const lockX = hh.a === "n" || hh.a === "s";
                      const lockY = hh.a === "e" || hh.a === "w";
                      return (
                        <Rect
                          key={`bh-${hh.a}`}
                          x={hh.x - r} y={hh.y - r}
                          width={r * 2} height={r * 2}
                          fill="#ffffff"
                          stroke={color}
                          strokeWidth={1.5 / view.scale}
                          draggable
                          dragBoundFunc={(pos) => ({
                            x: lockX ? view.x + (hh.x - r) * view.scale : pos.x,
                            y: lockY ? view.y + (hh.y - r) * view.scale : pos.y,
                          })}
                          onMouseEnter={(ev) => {
                            const stage = ev.target.getStage();
                            if (stage) stage.container().style.cursor = hh.cursor;
                          }}
                          onMouseLeave={(ev) => {
                            const stage = ev.target.getStage();
                            if (stage) stage.container().style.cursor = "";
                          }}
                          onDragStart={() => { wasDragRef.current = true; }}
                          onDragMove={(ev) => {
                            resizeBbox(hh.a, ev.target.x() + r, ev.target.y() + r);
                          }}
                          onDragEnd={() => { wasDragRef.current = true; flushEditBbox(); }}
                        />
                      );
                    })}
                  </>
                );
              })()}

              {/* Move / resize / rotate handles for selected OBB */}
              {selectedAnnId && editObb && (() => {
                const sel = annotations.find((x) => x.id === selectedAnnId);
                if (!sel || sel.geometry_type !== "obb") return null;
                const color = classMap.get(sel.class_id)?.color ?? "#3b82f6";
                const r = 5 / view.scale;
                const corners = obbCorners(editObb);
                const topMid: [number, number] = [
                  (corners[0][0] + corners[1][0]) / 2,
                  (corners[0][1] + corners[1][1]) / 2,
                ];
                const rot: [number, number] = rotatePoint(
                  editObb.cx,
                  editObb.cy - editObb.h / 2 - 28 / view.scale,
                  editObb.cx,
                  editObb.cy,
                  editObb.angle_rad,
                );
                return (
                  <>
                    <Line
                      points={[topMid[0], topMid[1], rot[0], rot[1]]}
                      stroke={color}
                      strokeWidth={1.25 / view.scale}
                      dash={[4 / view.scale, 3 / view.scale]}
                      listening={false}
                    />
                    {corners.map(([x, y], i) => (
                      <Rect
                        key={`obb-c-${i}`}
                        x={x - r} y={y - r}
                        width={r * 2} height={r * 2}
                        fill="#ffffff"
                        stroke={color}
                        strokeWidth={1.5 / view.scale}
                        draggable
                        onDragStart={() => { wasDragRef.current = true; }}
                        onDragMove={(ev) => updateObbSizeFromHandle(ev.target.x() + r, ev.target.y() + r)}
                        onDragEnd={() => { wasDragRef.current = true; flushEditObb(); }}
                      />
                    ))}
                    <Circle
                      key="obb-rot"
                      x={rot[0]} y={rot[1]} radius={r * 1.25}
                      fill="#f59e0b"
                      stroke="#ffffff"
                      strokeWidth={1.5 / view.scale}
                      draggable
                      onDragStart={() => { wasDragRef.current = true; }}
                      onDragMove={(ev) => updateObbRotation(ev.target.x(), ev.target.y())}
                      onDragEnd={() => { wasDragRef.current = true; flushEditObb(); }}
                    />
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
                <>
                  <Line
                    points={polyPoints.flat()}
                    stroke={activeClass.color}
                    strokeWidth={2.5 / view.scale}
                    closed={polyPoints.length >= 3}
                    fill={polyPoints.length >= 3 ? activeClass.color + "22" : undefined}
                    lineCap="round"
                    lineJoin="round"
                  />
                  {polyPoints.map(([px, py], i) => (
                    <Circle
                      key={`pg-${i}`}
                      x={px} y={py} radius={3.5 / view.scale}
                      fill={activeClass.color}
                      stroke="#ffffff"
                      strokeWidth={1 / view.scale}
                    />
                  ))}
                </>
              )}
              {polylinePoints.length > 0 && activeClass && (
                <>
                  <Line
                    points={polylinePoints.flat()}
                    stroke={activeClass.color}
                    strokeWidth={2.5 / view.scale}
                    closed={false}
                    lineCap="round"
                    lineJoin="round"
                    dash={[6 / view.scale, 3 / view.scale]}
                  />
                  {polylinePoints.map(([px, py], i) => (
                    <Circle
                      key={`pl-${i}`}
                      x={px} y={py} radius={3.5 / view.scale}
                      fill={activeClass.color}
                      stroke="#ffffff"
                      strokeWidth={1 / view.scale}
                    />
                  ))}
                </>
              )}
              {kpDrops.length > 0 && activeClass && (
                <>
                  {kpDrops.map(([px, py, v], i) => (
                    <Circle
                      key={`kpd-${i}`}
                      x={px} y={py} radius={5 / view.scale}
                      fill={v === 2 ? activeClass.color : "#000000"}
                      stroke={activeClass.color}
                      strokeWidth={2 / view.scale}
                      opacity={v === 2 ? 1 : 0.6}
                    />
                  ))}
                </>
              )}
              {bboxStart && bboxEnd && activeClass && (() => {
                const x = Math.min(bboxStart[0], bboxEnd[0]);
                const y = Math.min(bboxStart[1], bboxEnd[1]);
                const w = Math.abs(bboxEnd[0] - bboxStart[0]);
                const h = Math.abs(bboxEnd[1] - bboxStart[1]);
                const isObbPreview = tool === "obb";
                const rotX = x + w / 2;
                const rotY = y - 28 / view.scale;
                return (
                  <>
                    <Rect
                      x={x}
                      y={y}
                      width={w}
                      height={h}
                      stroke={isObbPreview ? "#f59e0b" : activeClass.color}
                      fill={isObbPreview ? "#f59e0b22" : undefined}
                      strokeWidth={2 / view.scale}
                      dash={[6 / view.scale, 4 / view.scale]}
                    />
                    {isObbPreview && (
                      <>
                        <Line
                          points={[x + w / 2, y, rotX, rotY]}
                          stroke="#f59e0b"
                          strokeWidth={1.5 / view.scale}
                          dash={[4 / view.scale, 3 / view.scale]}
                        />
                        <Circle
                          x={rotX}
                          y={rotY}
                          radius={6 / view.scale}
                          fill="#f59e0b"
                          stroke="#ffffff"
                          strokeWidth={1.5 / view.scale}
                        />
                      </>
                    )}
                  </>
                );
              })()}
              {/* Comment pins (existing) */}
              {comments.filter((c) => c.x != null && c.y != null).map((c) => {
                const isHovered = hoveredCommentId === c.id;
                const r = (isHovered ? 10 : 7) / view.scale;
                return (
                  <Circle
                    key={`pin-${c.id}`}
                    x={c.x as number}
                    y={c.y as number}
                    radius={r}
                    fill={c.resolved ? "#6b7280" : "#ec4899"}
                    stroke="#0a0a0a"
                    strokeWidth={2 / view.scale}
                    onMouseEnter={() => setHoveredCommentId(c.id)}
                    onMouseLeave={() => setHoveredCommentId((h) => (h === c.id ? null : h))}
                  />
                );
              })}
              {/* Pending pin (about to add) */}
              {pendingPin && (
                <Circle
                  x={pendingPin.x}
                  y={pendingPin.y}
                  radius={9 / view.scale}
                  fill="#fbbf24"
                  stroke="#0a0a0a"
                  strokeWidth={2 / view.scale}
                />
              )}
              {!isEditingAnnotation && tool === "brush" && brushPreview && (
                <>
                  <Circle
                    x={brushPreview.pt[0]}
                    y={brushPreview.pt[1]}
                    radius={brushSize}
                    fill={(brushPreview.erase ? "#ef4444" : activeClass?.color ?? "#22c55e") + "22"}
                    stroke="#0a0a0a"
                    strokeWidth={3 / view.scale}
                    opacity={0.95}
                  />
                  <Circle
                    x={brushPreview.pt[0]}
                    y={brushPreview.pt[1]}
                    radius={brushSize}
                    stroke={brushPreview.erase ? "#ef4444" : activeClass?.color ?? "#22c55e"}
                    strokeWidth={2 / view.scale}
                  />
                  <Circle
                    x={brushPreview.pt[0]}
                    y={brushPreview.pt[1]}
                    radius={2.5 / view.scale}
                    fill="#ffffff"
                    stroke="#0a0a0a"
                    strokeWidth={1 / view.scale}
                  />
                </>
              )}
            </Layer>
          </Stage>

          {/* Pending comment composer */}
          {!isEditingAnnotation && pendingPin && (
            <div className="absolute bottom-24 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 rounded-lg p-2 flex gap-2 items-center text-sm shadow-xl z-10">
              <span className="text-amber-400 px-2 text-xs">📍 ({pendingPin.x},{pendingPin.y})</span>
              <input
                autoFocus
                value={pendingBody}
                onChange={(e) => setPendingBody(e.target.value)}
                onKeyDown={async (e) => {
                  if (e.key === "Escape") { setPendingPin(null); setPendingBody(""); }
                  else if (e.key === "Enter" && pendingBody.trim()) {
                    try {
                      await api.createComment(imageId, { body: pendingBody.trim(), x: pendingPin.x, y: pendingPin.y });
                      setPendingPin(null); setPendingBody("");
                      setComments(await api.listComments(imageId));
                    } catch (err) { setError((err as Error).message); }
                  }
                }}
                placeholder="Type comment, Enter to save…"
                className="bg-neutral-800 px-3 py-1 rounded text-sm w-72 outline-none focus:ring-1 focus:ring-amber-500"
              />
              <button
                onClick={() => { setPendingPin(null); setPendingBody(""); }}
                className="text-neutral-500 hover:text-neutral-200 px-2"
                title="Cancel (Esc)"
              >×</button>
            </div>
          )}

          {/* Pending action bar */}
          {!isEditingAnnotation && pending && (
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
          {!isEditingAnnotation && tool === "polygon" && polyPoints.length > 0 && (
            <div className="absolute bottom-24 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-xs text-neutral-300 z-10">
              Polygon · {polyPoints.length} point{polyPoints.length === 1 ? "" : "s"} ·{" "}
              {polyPoints.length >= 3 ? "closed shape ready, press Enter to save" : "click at least 3 points"} · Esc to clear
            </div>
          )}
          {!isEditingAnnotation && tool === "polyline" && polylinePoints.length > 0 && (
            <div className="absolute bottom-24 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-xs text-neutral-300 z-10">
              Polyline · {polylinePoints.length} point{polylinePoints.length === 1 ? "" : "s"} ·{" "}
              {polylinePoints.length >= 2 ? "open line ready, press Enter to save" : "click at least one more point"} · Esc to clear
            </div>
          )}
          {!isEditingAnnotation && tool === "obb" && (
            <div className="absolute bottom-24 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-xs text-neutral-300 z-10">
              Rotated box · drag to place · release, then drag the orange dot to rotate
            </div>
          )}
          {isEditingAnnotation && editObb && (
            <div className="absolute bottom-24 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 rounded-lg px-3 py-2 text-xs text-neutral-300 z-10">
              Rotated box edit · drag body to move · white corners resize · orange dot rotates
            </div>
          )}
          {!isEditingAnnotation && tool === "brush" && (
            <div className="absolute bottom-24 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 rounded-lg p-2 flex gap-3 items-center text-xs shadow-xl z-10">
              <span className="text-neutral-300">Brush</span>
              <label className="flex items-center gap-2 text-neutral-400">
                Size
                <input
                  type="range"
                  min={2}
                  max={120}
                  value={brushSize}
                  onChange={(e) => setBrushSize(Number(e.target.value))}
                  className="w-32"
                />
                <span className="w-8 text-right text-neutral-300">{brushSize}px</span>
              </label>
              <span className="text-neutral-500">shift = erase · [ ] resize</span>
              <button
                onClick={commitBrush}
                disabled={!hasBrushStrokes}
                className="bg-emerald-500 text-neutral-950 px-3 py-1 rounded font-medium hover:bg-emerald-400 disabled:opacity-40 disabled:hover:bg-emerald-500"
              >
                Commit (Enter)
              </button>
              <button
                onClick={clearMask}
                disabled={!hasBrushStrokes}
                className="text-neutral-400 px-2 hover:text-neutral-100 disabled:opacity-40"
              >
                Clear (Esc)
              </button>
            </div>
          )}

          {/* Zoom / pan mini-toolbar (always visible) */}
          <div className="absolute bottom-20 right-3 bg-neutral-900/90 border border-neutral-700 rounded-lg px-2 py-1 flex items-center gap-1 text-xs shadow-xl z-10">
            <button
              onClick={fitToScreen}
              title="Fit image to screen (F)"
              className="px-2 py-1 rounded hover:bg-neutral-800 text-neutral-200"
            >
              ⤢ Fit
            </button>
            <button
              onClick={zoomTo100}
              title="Reset to 100% (0)"
              className="px-2 py-1 rounded hover:bg-neutral-800 text-neutral-200"
            >
              1:1
            </button>
            <span className="text-neutral-400 px-2 tabular-nums w-14 text-right">
              {Math.round(view.scale * 100)}%
            </span>
            <button
              onClick={() => setShowHelp((v) => !v)}
              title="Keyboard shortcuts & tips (? or H)"
              className={`px-2 py-1 rounded text-neutral-200 ${showHelp ? "bg-emerald-500 text-neutral-950" : "hover:bg-neutral-800"}`}
            >
              ? Help
            </button>
          </div>
          {showHelp && <Cheatsheet onClose={() => setShowHelp(false)} />}
          {spacePan && (
            <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-neutral-900 border border-neutral-700 text-neutral-200 text-xs px-3 py-1 rounded-full shadow z-10">
              ✋ Pan (release Space to resume)
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

          {selectedAnn && (() => {
            const props = classPropsCache[selectedAnn.class_id] ?? [];
            const cls = classMap.get(selectedAnn.class_id);
            return (
              <div className="p-3 border-b border-neutral-800 bg-neutral-900/60">
                <div className="text-xs uppercase tracking-wider text-neutral-500 mb-2 flex items-center gap-2">
                  <span>Properties</span>
                  <span className="lowercase text-[10px] normal-case text-neutral-600">
                    · {cls?.name ?? "?"}
                  </span>
                </div>
                {props.length === 0 ? (
                  <div className="text-xs text-neutral-600">
                    No properties defined for this class.{" "}
                    <Link to={`/projects/${projectId}`} className="text-emerald-400 hover:underline">
                      Add some →
                    </Link>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {props.map((p) => (
                      <PropertyControl
                        key={p.id}
                        property={p}
                        value={selectedAnn.properties?.[p.id] ?? (p.type === "multi" ? [] : "")}
                        onChange={(v) => setAnnotationProperty(selectedAnn.id, p.id, v)}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })()}

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
                      onClick={() => selectAnnotation(a)}
                      onMouseEnter={() => setHoverAnnId(a.id)}
                      onMouseLeave={() => setHoverAnnId(null)}
                      className={`flex items-center gap-2 px-2 py-1.5 rounded text-sm group cursor-pointer select-none ${
                        selectedAnnId === a.id
                          ? "bg-emerald-500/15 ring-1 ring-emerald-500/50 text-neutral-100"
                          : hoverAnnId === a.id
                            ? "bg-neutral-800"
                            : "hover:bg-neutral-800/50"
                      }`}
                      title="Click to select and highlight this annotation"
                    >
                      <span className="w-2.5 h-2.5 rounded" style={{ background: c?.color ?? "#888" }} />
                      <span className="flex-1 truncate">{c?.name ?? "?"} #{i + 1}</span>
                      <span className="text-[10px] text-neutral-500">{a.geometry_type}</span>
                      {a.source === "model" && (
                        <span className="text-[10px] text-emerald-400" title={`SAM2 confidence ${a.confidence}`}>✨</span>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteAnnotation(a.id);
                        }}
                        className="text-neutral-600 hover:text-rose-400 opacity-0 group-hover:opacity-100"
                      >✕</button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <CommentPanel
            imageId={imageId}
            comments={comments}
            onChange={(cs) => setComments(cs)}
            currentUserId={null}
            hoveredCommentId={hoveredCommentId}
            setHoveredCommentId={setHoveredCommentId}
          />

          <div className="p-3 border-t border-neutral-800 text-[11px] text-neutral-500 space-y-1">
            <div><kbd className="kbd">V</kbd> pan · <kbd className="kbd">A</kbd> auto · <kbd className="kbd">P</kbd> polygon · <kbd className="kbd">L</kbd> polyline · <kbd className="kbd">B</kbd> bbox</div>
            <div><kbd className="kbd">O</kbd> rotated box · <kbd className="kbd">R</kbd> brush · <kbd className="kbd">K</kbd> keypoints · <kbd className="kbd">T</kbd> comment</div>
            <div><kbd className="kbd">Enter</kbd> save current drawing · <kbd className="kbd">Esc</kbd> cancel · <kbd className="kbd">←/→</kbd> navigate</div>
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

const SHORTCUT_GROUPS: { title: string; rows: [string, string][] }[] = [
  {
    title: "Tools",
    rows: [
      ["V", "Pan"],
      ["A", "Auto-annotate (SAM2)"],
      ["P", "Polygon"],
      ["L", "Polyline"],
      ["B", "Bounding box"],
      ["O", "Rotated bounding box (OBB)"],
      ["R", "Brush"],
      ["K", "Keypoints"],
      ["T", "Comment pin"],
      ["1–9", "Pick class by index"],
    ],
  },
  {
    title: "Canvas",
    rows: [
      ["Hold Space", "Temporary pan (any tool)"],
      ["Scroll", "Zoom in / out at cursor"],
      ["F", "Fit image to screen"],
      ["0", "Reset to 100%"],
      ["+ / -", "Zoom in / out"],
      ["← / →", "Previous / next image"],
    ],
  },
  {
    title: "Auto-annotate (A)",
    rows: [
      ["Click", "One-point prompt → SAM2 mask"],
      ["Shift-click", "Add positive point"],
      ["Alt-click", "Add negative (subtract) point"],
      ["Drag", "Box prompt (replaces points)"],
      ["Enter", "Save predicted polygon"],
      ["Esc", "Discard prompts / preview"],
    ],
  },
  {
    title: "Polygon (P)",
    rows: [
      ["Click", "Add vertex"],
      ["Enter", "Close & save (≥ 3 pts)"],
      ["Click annotation", "Edit vertices"],
      ["Drag handle", "Move vertex"],
      ["Click white midpoint", "Insert vertex"],
      ["Shift/Alt-click handle", "Delete vertex (min 3)"],
    ],
  },
  {
    title: "Bounding box (B)",
    rows: [
      ["Drag", "Draw new bbox"],
      ["Click bbox", "Select & show 8 handles"],
      ["Drag corner / edge", "Resize"],
      ["Drag body", "Move"],
      ["Del / Backspace", "Delete (when hovered)"],
    ],
  },
  {
    title: "Polyline (L), OBB (O), Keypoints (K)",
    rows: [
      ["L + Click", "Add polyline vertex"],
      ["L + Enter", "Save open polyline (>= 2 pts)"],
      ["O + Drag", "Draw rotated bbox (axis-aligned initial angle)"],
      ["K + Click", "Drop next keypoint in schema order"],
      ["K + Cmd/Ctrl-click", "Drop occluded keypoint (COCO v=1)"],
      ["Esc", "Clear current in-progress polyline/keypoints"],
    ],
  },
  {
    title: "Brush (R)",
    rows: [
      ["Drag", "Paint mask"],
      ["Shift-drag", "Erase"],
      ["[ / ]", "Smaller / larger brush"],
      ["Enter", "Commit → polygon"],
      ["Esc", "Clear brush"],
    ],
  },
  {
    title: "Selection & history",
    rows: [
      ["Hover + Del/Backspace", "Delete annotation"],
      ["⌘/Ctrl-Z", "Undo (last 20)"],
      ["⌘/Ctrl-Shift-Z", "Redo"],
      ["⌘/Ctrl-Y", "Redo (alt)"],
    ],
  },
  {
    title: "Top bar",
    rows: [
      ["Next uncertain ✨", "Jump to least-sure unannotated image"],
      ["Model picker", "Predict with base SAM2 or fine-tuned LoRA"],
    ],
  },
];

function Cheatsheet({ onClose }: { onClose: () => void }) {
  return (
    <div
      className="absolute inset-0 z-20 grid place-items-center bg-neutral-950/40 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-neutral-900 border border-neutral-700 rounded-xl shadow-2xl max-w-3xl w-[90vw] max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-4 border-b border-neutral-800 sticky top-0 bg-neutral-900">
          <div className="text-base font-semibold text-neutral-100">Anvil shortcuts &amp; tips</div>
          <button
            onClick={onClose}
            className="text-neutral-400 hover:text-neutral-100 text-lg"
            title="Close (Esc / ?)"
          >
            ×
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-4 p-5 text-xs">
          {SHORTCUT_GROUPS.map((g) => (
            <div key={g.title}>
              <div className="text-emerald-400 font-semibold uppercase tracking-wide text-[11px] mb-2">
                {g.title}
              </div>
              <table className="w-full">
                <tbody>
                  {g.rows.map(([key, desc]) => (
                    <tr key={key} className="align-top">
                      <td className="py-1 pr-3 whitespace-nowrap">
                        <kbd className="px-1.5 py-0.5 rounded bg-neutral-800 border border-neutral-700 text-neutral-200 font-mono text-[11px]">
                          {key}
                        </kbd>
                      </td>
                      <td className="py-1 text-neutral-300">{desc}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
        <div className="px-5 pb-4 text-[11px] text-neutral-500">
          Press <kbd className="px-1 rounded bg-neutral-800 border border-neutral-700">?</kbd> or{" "}
          <kbd className="px-1 rounded bg-neutral-800 border border-neutral-700">H</kbd> to toggle this panel.
        </div>
      </div>
    </div>
  );
}

function PropertyControl({
  property, value, onChange,
}: {
  property: ClassProperty;
  value: string | string[];
  onChange: (v: string | string[] | null) => void;
}) {
  const label = (
    <div className="text-xs text-neutral-300 mb-1 flex items-center gap-1.5">
      <span className="font-medium">{property.name}</span>
      {property.required && <span className="text-amber-400 text-[10px]">required</span>}
      <span className="text-neutral-600 text-[10px] ml-auto">{property.type}</span>
    </div>
  );

  if (property.type === "single") {
    const v = typeof value === "string" ? value : "";
    return (
      <div>
        {label}
        <div className="flex flex-wrap gap-1">
          {property.options.map((opt) => (
            <button
              key={opt}
              onClick={() => onChange(v === opt ? null : opt)}
              className={`px-2 py-1 rounded text-xs border ${
                v === opt
                  ? "bg-emerald-500 text-neutral-950 border-emerald-500 font-medium"
                  : "bg-neutral-900 border-neutral-800 text-neutral-300 hover:border-neutral-700"
              }`}
            >
              {opt}
            </button>
          ))}
        </div>
      </div>
    );
  }

  if (property.type === "multi") {
    const arr = Array.isArray(value) ? value : [];
    return (
      <div>
        {label}
        <div className="flex flex-wrap gap-1">
          {property.options.map((opt) => {
            const on = arr.includes(opt);
            return (
              <button
                key={opt}
                onClick={() => onChange(on ? arr.filter((x) => x !== opt) : [...arr, opt])}
                className={`px-2 py-1 rounded text-xs border ${
                  on
                    ? "bg-emerald-500 text-neutral-950 border-emerald-500 font-medium"
                    : "bg-neutral-900 border-neutral-800 text-neutral-300 hover:border-neutral-700"
                }`}
              >
                {on ? "✓ " : ""}{opt}
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  // text
  const v = typeof value === "string" ? value : "";
  return (
    <div>
      {label}
      <input
        className="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1 text-xs focus:border-emerald-500 outline-none"
        defaultValue={v}
        onBlur={(e) => {
          const next = e.target.value;
          if (next !== v) onChange(next || null);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        }}
      />
    </div>
  );
}

// ---------------- Review controls (top bar) ----------------

const REVIEW_STYLES: Record<string, { label: string; bg: string; fg: string; dot: string }> = {
  new:           { label: "New",         bg: "bg-neutral-800", fg: "text-neutral-300", dot: "bg-neutral-500" },
  in_progress:   { label: "In progress", bg: "bg-blue-900/40", fg: "text-blue-300",    dot: "bg-blue-400" },
  submitted:     { label: "Submitted",   bg: "bg-amber-900/40",fg: "text-amber-300",   dot: "bg-amber-400" },
  approved:      { label: "Approved",    bg: "bg-emerald-900/40", fg: "text-emerald-300", dot: "bg-emerald-400" },
  rejected:      { label: "Rejected",    bg: "bg-rose-900/40", fg: "text-rose-300",    dot: "bg-rose-400" },
};

function ReviewControls({ image, onReload }: { image: ImageRow | null; onReload: () => void | Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const role = useAuth((s) => s.user?.role);
  const canSubmit = isLabeler(role);
  const canReview = isReviewer(role);
  if (!image) return null;
  const st = REVIEW_STYLES[image.review_status] ?? REVIEW_STYLES.new;

  async function act(action: "submit" | "approve" | "reject" | "reopen", reason?: string) {
    setBusy(true);
    try {
      await api.reviewAction(image!.id, { action, reason });
      if (action === "reject") setRejectOpen(false);
      await onReload();
    } catch (e) {
      toast.error(e, "Review action failed");
    } finally {
      setBusy(false);
    }
  }

  const status = image.review_status;
  return (
    <>
      <div className="flex items-center gap-1">
        <span
          title={image.review_reason ? `Reason: ${image.review_reason}` : undefined}
          className={`text-[11px] px-2 py-1 rounded-full inline-flex items-center gap-1.5 ${st.bg} ${st.fg}`}
        >
          <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
          {st.label}
        </span>
        {canSubmit && (status === "new" || status === "in_progress" || status === "rejected") && (
          <button
            onClick={() => act("submit")} disabled={busy}
            className="text-xs bg-amber-500 hover:bg-amber-400 disabled:bg-neutral-700 text-neutral-950 font-medium px-3 py-1 rounded"
            title="Submit for review"
          >Submit ▸</button>
        )}
        {canReview && status === "submitted" && (
          <>
            <button
              onClick={() => act("approve")} disabled={busy}
              className="text-xs bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 font-medium px-3 py-1 rounded"
            >✓ Approve</button>
            <button
              onClick={() => setRejectOpen(true)} disabled={busy}
              className="text-xs bg-rose-500 hover:bg-rose-400 disabled:bg-neutral-700 text-white font-medium px-3 py-1 rounded"
            >✕ Reject</button>
          </>
        )}
        {canReview && (status === "approved" || status === "rejected") && (
          <button
            onClick={() => act("reopen")} disabled={busy}
            className="text-xs bg-neutral-700 hover:bg-neutral-600 disabled:opacity-50 text-neutral-100 px-3 py-1 rounded"
            title="Reopen for editing"
          >↺ Reopen</button>
        )}
      </div>
      <RejectReasonDialog
        open={rejectOpen}
        busy={busy}
        onCancel={() => setRejectOpen(false)}
        onConfirm={(reason) => act("reject", reason)}
      />
    </>
  );
}

// ---------------- Comments panel (right sidebar) ----------------

function CommentPanel({
  imageId, comments, onChange, hoveredCommentId, setHoveredCommentId,
}: {
  imageId: string;
  comments: CommentRow[];
  onChange: (cs: CommentRow[]) => void;
  currentUserId: string | null;
  hoveredCommentId: string | null;
  setHoveredCommentId: (id: string | null) => void;
}) {
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<CommentRow | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function refresh() { onChange(await api.listComments(imageId)); }

  async function addGeneral() {
    const t = body.trim(); if (!t) return;
    setBusy(true);
    try {
      await api.createComment(imageId, { body: t, x: null, y: null });
      setBody(""); await refresh();
    } catch (e) { toast.error(e); }
    finally { setBusy(false); }
  }

  async function toggleResolved(c: CommentRow) {
    try { await api.patchComment(c.id, { resolved: !c.resolved }); await refresh(); }
    catch (e) { toast.error(e); }
  }

  async function del(c: CommentRow) {
    setDeleteTarget(c);
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.deleteComment(deleteTarget.id);
      setDeleteTarget(null);
      await refresh();
    }
    catch (e) { toast.error(e); }
    finally { setDeleting(false); }
  }

  const open = comments.filter((c) => !c.resolved);
  const resolved = comments.filter((c) => c.resolved);

  return (
    <>
      <div className="border-b border-neutral-800 p-3 max-h-72 overflow-y-auto">
        <div className="text-xs uppercase tracking-wider text-neutral-500 mb-2 flex items-center justify-between">
          <span>Comments</span>
          <span className="text-[10px] normal-case tracking-normal text-neutral-600">
            {open.length} open · {resolved.length} resolved
          </span>
        </div>
        <div className="flex gap-1 mb-2">
          <input
            value={body}
            onChange={(e) => setBody(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") addGeneral(); }}
            placeholder="Add a note (or use 💬 tool to pin)…"
            className="flex-1 bg-neutral-800 px-2 py-1 rounded text-xs outline-none focus:ring-1 focus:ring-emerald-500"
            disabled={busy}
          />
          <button
            onClick={addGeneral}
            disabled={busy || !body.trim()}
            className="text-xs bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white px-2 rounded"
          >+</button>
        </div>
        {comments.length === 0 ? (
          <div className="text-[11px] text-neutral-600 italic">No comments yet.</div>
        ) : (
          <ul className="space-y-1.5">
            {comments.map((c) => (
              <li
                key={c.id}
                onMouseEnter={() => setHoveredCommentId(c.id)}
                onMouseLeave={() => setHoveredCommentId(hoveredCommentId === c.id ? null : hoveredCommentId)}
                className={`group rounded px-2 py-1.5 text-xs ${c.resolved ? "bg-neutral-900 text-neutral-500" : "bg-neutral-800 text-neutral-200"} ${hoveredCommentId === c.id ? "ring-1 ring-pink-500" : ""}`}
              >
                <div className="flex items-start gap-1">
                  <span className="text-[10px] mt-0.5">{c.x != null ? "📍" : "💬"}</span>
                  <div className="flex-1 min-w-0">
                    <div className={c.resolved ? "line-through" : ""}>{c.body}</div>
                    <div className="text-[10px] text-neutral-600 mt-0.5">
                      {c.author_name} · {new Date(c.created_at).toLocaleString()}
                      {c.x != null && <> · ({c.x},{c.y})</>}
                    </div>
                  </div>
                  <button
                    onClick={() => toggleResolved(c)}
                    title={c.resolved ? "Reopen" : "Mark resolved"}
                    className="text-neutral-500 hover:text-emerald-400 opacity-0 group-hover:opacity-100"
                  >{c.resolved ? "↺" : "✓"}</button>
                  <button
                    onClick={() => del(c)}
                    title="Delete"
                    className="text-neutral-500 hover:text-rose-400 opacity-0 group-hover:opacity-100"
                  >✕</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete comment?"
        description="Delete this comment? This cannot be undone."
        confirmLabel="Delete comment"
        tone="danger"
        busy={deleting}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={confirmDelete}
      />
    </>
  );
}
