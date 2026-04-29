import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, downloadCocoExport, fileUrl, type ImageListOpts, type ImageSort } from "../lib/api";
import { toast } from "../lib/toast";
import { RejectReasonDialog } from "../components/RejectReasonDialog";
import type { ClassRow, Dataset, ImageRow, ReviewStatus } from "../types";

const SORT_LABELS: Record<ImageSort, string> = {
  oldest: "Oldest first",
  recent: "Newest first",
  filename: "Filename A→Z",
  most_labels: "Most labels",
  fewest_labels: "Fewest labels",
  uncertainty: "Highest uncertainty",
};

export function DatasetDetailPage() {
  const { projectId = "", datasetId = "" } = useParams();
  const [search, setSearch] = useSearchParams();

  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [images, setImages] = useState<ImageRow[]>([]);
  const [allDatasets, setAllDatasets] = useState<Dataset[]>([]);
  const [classes, setClasses] = useState<ClassRow[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const cocoInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [filter, setFilter] = useState<"all" | "ready" | "uploaded" | "embedding" | "failed">("all");
  const [total, setTotal] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [bulkRejectOpen, setBulkRejectOpen] = useState(false);
  const [bulkReviewBusy, setBulkReviewBusy] = useState(false);

  // Selection state for bulk ops (persists across pagination loadMore).
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const lastSelectedRef = useRef<string | null>(null);

  const PAGE = 200;

  // URL-derived filter state. Centralized so back/forward and shared links
  // restore the exact view.
  const reviewFilter = (search.get("review") ?? "all") as "all" | ReviewStatus;
  const hasAnnotations = search.get("has") === "yes" ? true : search.get("has") === "no" ? false : undefined;
  const classFilter = useMemo(() => search.getAll("class").filter(Boolean), [search]);
  const sort: ImageSort = (search.get("sort") as ImageSort) ?? "oldest";

  const setParam = useCallback((key: string, val: string | null) => {
    setSearch((sp) => {
      const next = new URLSearchParams(sp);
      if (val == null || val === "") next.delete(key);
      else next.set(key, val);
      return next;
    }, { replace: true });
  }, [setSearch]);

  const toggleClassFilter = useCallback((cid: string) => {
    setSearch((sp) => {
      const next = new URLSearchParams(sp);
      const cur = new Set(next.getAll("class"));
      if (cur.has(cid)) cur.delete(cid);
      else cur.add(cid);
      next.delete("class");
      cur.forEach((c) => next.append("class", c));
      return next;
    }, { replace: true });
  }, [setSearch]);

  const buildOpts = useCallback((): ImageListOpts => ({
    review_status: reviewFilter !== "all" ? reviewFilter : undefined,
    has_annotations: hasAnnotations,
    class_id: classFilter.length ? classFilter : undefined,
    sort,
  }), [reviewFilter, hasAnnotations, classFilter, sort]);

  const reload = useCallback(async () => {
    const [d, paged, cls, ds] = await Promise.all([
      api.getDataset(projectId, datasetId),
      api.listImagesPaged(projectId, datasetId, { ...buildOpts(), limit: PAGE, offset: 0 }),
      api.listClasses(projectId).catch(() => [] as ClassRow[]),
      api.listDatasets(projectId).catch(() => [] as Dataset[]),
    ]);
    setDataset(d); setImages(paged.items); setTotal(paged.total);
    setClasses(cls); setAllDatasets(ds);
  }, [projectId, datasetId, buildOpts]);

  useEffect(() => { reload(); }, [reload]);

  async function loadMore() {
    if (loadingMore || images.length >= total) return;
    setLoadingMore(true);
    try {
      const paged = await api.listImagesPaged(projectId, datasetId, { ...buildOpts(), limit: PAGE, offset: images.length });
      setImages((prev) => [...prev, ...paged.items]);
      setTotal(paged.total);
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    const pending = images.some((i) => i.status === "uploaded" || i.status === "embedding");
    if (!pending) return;
    const t = setInterval(reload, 1500);
    return () => clearInterval(t);
  }, [images, reload]);

  useEffect(() => {
    function k(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (t && /^(input|textarea|select)$/i.test(t.tagName)) return;
      if (e.key === "Escape" && selected.size > 0) {
        setSelected(new Set());
      }
    }
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [selected.size]);

  async function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    const arr = Array.from(files);
    setUploading(true); setUploadProgress({ done: 0, total: arr.length });
    // upload in batches of 8 for snappier feedback
    const BATCH = 8;
    try {
      for (let i = 0; i < arr.length; i += BATCH) {
        const batch = arr.slice(i, i + BATCH);
        await api.uploadImages(projectId, datasetId, batch);
        setUploadProgress({ done: Math.min(i + batch.length, arr.length), total: arr.length });
        await reload();
      }
    } finally {
      setUploading(false); setUploadProgress(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    const files = e.dataTransfer.files;
    if (!files || files.length === 0) return;
    // If a single .zip dropped, treat as COCO import
    if (files.length === 1 && /\.zip$/i.test(files[0].name)) {
      const f = files[0];
      setImporting(true);
      api.importCocoZip(projectId, datasetId, f)
        .then(async (r) => {
          toast.success(
            `Imported ${r.images_imported} images`,
            `${r.polygons_imported} polygons, ${r.bboxes_imported} bboxes; ` +
            `${r.new_classes} new classes, ${r.matched_existing_classes} reused.`
          );
          await reload();
        })
        .catch((err) => toast.error(err, "Import failed"))
        .finally(() => setImporting(false));
      return;
    }
    upload(files);
  }

  const counts = {
    all: images.length,
    ready: images.filter((i) => i.status === "ready").length,
    embedding: images.filter((i) => i.status === "embedding" || i.status === "uploaded").length,
    failed: images.filter((i) => i.status === "failed").length,
  };
  const filtered = images.filter((i) => filter === "all" || i.status === filter);
  const visibleIds = filtered.map((i) => i.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));

  function toggleOne(id: string, ev?: React.MouseEvent) {
    if (ev?.shiftKey && lastSelectedRef.current) {
      const a = visibleIds.indexOf(lastSelectedRef.current);
      const b = visibleIds.indexOf(id);
      if (a >= 0 && b >= 0) {
        const [lo, hi] = a < b ? [a, b] : [b, a];
        const range = visibleIds.slice(lo, hi + 1);
        setSelected((prev) => {
          const next = new Set(prev);
          range.forEach((rid) => next.add(rid));
          return next;
        });
        lastSelectedRef.current = id;
        return;
      }
    }
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    lastSelectedRef.current = id;
  }

  async function bulkDelete() {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} image(s)? This cannot be undone.`)) return;
    try {
      const r = await api.bulkDeleteImages(projectId, datasetId, [...selected]);
      const okN = r.ok.length;
      if (r.failed.length > 0) {
        toast.warn(`Deleted ${okN} of ${selected.size}`, `${r.failed.length} failed (e.g. ${r.failed[0].reason})`);
      } else {
        toast.success(`Deleted ${okN} image(s)`);
      }
      setSelected(new Set());
      await reload();
    } catch (e) { toast.error(e, "Bulk delete failed"); }
  }

  async function bulkReview(action: "reset" | "reopen" | "reject", reason?: string) {
    if (selected.size === 0) return;
    setBulkReviewBusy(true);
    try {
      const r = await api.bulkReviewImages(projectId, datasetId, {
        image_ids: [...selected], action, reason,
      });
      const okN = r.ok.length;
      if (r.failed.length > 0) {
        toast.warn(`${action}: ${okN} of ${selected.size}`, `${r.failed.length} failed (e.g. ${r.failed[0].reason})`);
      } else {
        toast.success(`${action}: ${okN} image(s)`);
      }
      if (action === "reject") setBulkRejectOpen(false);
      setSelected(new Set());
      await reload();
    } catch (e) { toast.error(e, `Bulk ${action} failed`); }
    finally { setBulkReviewBusy(false); }
  }

  async function bulkMove(targetDatasetId: string) {
    if (selected.size === 0 || !targetDatasetId) return;
    try {
      const r = await api.bulkMoveImages(projectId, datasetId, {
        image_ids: [...selected], target_dataset_id: targetDatasetId,
      });
      const okN = r.ok.length;
      if (r.failed.length > 0) {
        toast.warn(`Moved ${okN} of ${selected.size}`, `${r.failed.length} failed (e.g. ${r.failed[0].reason})`);
      } else {
        toast.success(`Moved ${okN} image(s)`);
      }
      setSelected(new Set());
      await reload();
    } catch (e) { toast.error(e, "Bulk move failed"); }
  }

  async function bulkDeleteAnnotationsByClass(classId: string) {
    if (selected.size === 0 || !classId) return;
    const cls = classes.find((c) => c.id === classId);
    if (!confirm(`Delete all "${cls?.name ?? classId}" annotations from ${selected.size} image(s)?`)) return;
    try {
      const r = await api.bulkDeleteAnnotationsByClass(projectId, datasetId, {
        class_id: classId, image_ids: [...selected],
      });
      const okN = r.ok.length;
      if (r.failed.length > 0) {
        toast.warn(`Cleared on ${okN} of ${selected.size}`, `${r.failed.length} failed/skipped`);
      } else {
        toast.success(`Cleared "${cls?.name ?? classId}" on ${okN} image(s)`);
      }
      setSelected(new Set());
      await reload();
    } catch (e) { toast.error(e, "Bulk class delete failed"); }
  }

  if (!dataset) return <div className="p-8 text-neutral-500">loading…</div>;

  return (
    <>
      <div onDragOver={(e) => e.preventDefault()} onDrop={onDrop} className="min-h-full">
      <div className="border-b border-neutral-800 px-8 pt-6 pb-4">
        <div className="text-xs text-neutral-500 mb-1">
          <Link to="/projects" className="hover:text-neutral-300">Projects</Link> ·{" "}
          <Link to={`/projects/${projectId}`} className="hover:text-neutral-300">project</Link> /{" "}
          {dataset.slug}
        </div>
        <div className="flex items-end justify-between">
          <div>
            <h1 className="text-2xl font-semibold">{dataset.name}</h1>
            <div className="text-xs text-neutral-500 mt-1">
              {counts.all} images · {counts.ready} ready · {counts.embedding} processing · {counts.failed} failed
            </div>
          </div>
          <div className="flex gap-2">
            <input ref={fileInput} type="file" multiple accept="image/*" hidden
              onChange={(e) => upload(e.target.files)} />
            <input ref={cocoInput} type="file" accept=".zip,application/zip" hidden
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                setImporting(true);
                try {
                  const r = await api.importCocoZip(projectId, datasetId, f);
                  const skipped: string[] = [];
                  if (r.images_skipped_missing_or_invalid) skipped.push(`${r.images_skipped_missing_or_invalid} images (missing/invalid)`);
                  if (r.rle_segmentations_skipped) skipped.push(`${r.rle_segmentations_skipped} RLE segmentations (not yet supported)`);
                  toast.success(
                    `Imported ${r.images_imported} images`,
                    `${r.polygons_imported} polygons, ${r.bboxes_imported} bboxes; ` +
                    `${r.new_classes} new classes, ${r.matched_existing_classes} reused.` +
                    (skipped.length ? ` Skipped ${skipped.join("; ")}.` : "")
                  );
                  await reload();
                } catch (err) {
                  toast.error(err, "Import failed");
                } finally {
                  setImporting(false);
                  if (cocoInput.current) cocoInput.current.value = "";
                }
              }} />
            <button
              onClick={() => cocoInput.current?.click()}
              disabled={importing}
              title="Drag-drop or pick a COCO export ZIP (annotations.json + images) to bootstrap this dataset"
              className="bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-100 font-medium px-4 py-2 rounded text-sm border border-neutral-700"
            >
              {importing ? "Importing…" : "Import COCO"}
            </button>
            <button
              onClick={async () => {
                try {
                  const opts = reviewFilter !== "all" ? { review_status: reviewFilter } : undefined;
                  const suffix = reviewFilter !== "all" ? `-${reviewFilter}` : "";
                  await downloadCocoExport(projectId, datasetId, `${dataset.slug}${suffix}-coco.zip`, opts);
                } catch (e) {
                  toast.error(e, "Export failed");
                }
              }}
              disabled={counts.ready === 0}
              title={reviewFilter === "all"
                ? "Download annotations.json (COCO format) + images as a ZIP"
                : `Export only images with review status: ${reviewFilter}`}
              className="bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-100 font-medium px-4 py-2 rounded text-sm border border-neutral-700"
            >
              Export COCO{reviewFilter !== "all" ? ` (${reviewFilter})` : ""}
            </button>
            <button
              onClick={() => fileInput.current?.click()}
              disabled={uploading}
              className="bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 font-medium px-4 py-2 rounded text-sm"
            >
              {uploading ? `Uploading ${uploadProgress?.done}/${uploadProgress?.total}…` : "Upload images"}
            </button>
          </div>
        </div>

        <div className="mt-4 flex gap-1 flex-wrap items-center">
          {(["all", "ready", "embedding", "failed"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1 text-xs rounded ${
                filter === f ? "bg-neutral-700 text-neutral-100" : "bg-neutral-900 text-neutral-400 hover:text-neutral-200"
              }`}
            >
              {f} <span className="ml-1 text-neutral-500">{counts[f as keyof typeof counts] ?? counts.all}</span>
            </button>
          ))}
          <span className="mx-2 h-4 w-px bg-neutral-800" />
          <select
            value={sort}
            onChange={(e) => setParam("sort", e.target.value === "oldest" ? null : e.target.value)}
            className="bg-neutral-900 border border-neutral-800 text-neutral-200 text-xs rounded px-2 py-1"
            title="Sort order (server-side)"
          >
            {(Object.keys(SORT_LABELS) as ImageSort[]).map((s) => (
              <option key={s} value={s}>{SORT_LABELS[s]}</option>
            ))}
          </select>
          <select
            value={search.get("has") ?? ""}
            onChange={(e) => setParam("has", e.target.value || null)}
            className="bg-neutral-900 border border-neutral-800 text-neutral-200 text-xs rounded px-2 py-1"
            title="Has annotations filter (server-side)"
          >
            <option value="">all (annotated + unannotated)</option>
            <option value="yes">has annotations</option>
            <option value="no">no annotations</option>
          </select>
        </div>

        <div className="mt-2 flex gap-1 items-center flex-wrap">
          <span className="text-[10px] uppercase tracking-wider text-neutral-600 mr-1">Review</span>
          {(["all", "new", "in_progress", "submitted", "approved", "rejected"] as const).map((f) => {
            const labels: Record<typeof f, string> = {
              all: "all", new: "new", in_progress: "in progress",
              submitted: "submitted", approved: "approved", rejected: "rejected",
            };
            const active = reviewFilter === f;
            const ringColor = {
              all: "", new: "", in_progress: "ring-blue-500",
              submitted: "ring-amber-500", approved: "ring-emerald-500", rejected: "ring-rose-500",
            }[f];
            return (
              <button
                key={f}
                onClick={() => setParam("review", f === "all" ? null : f)}
                className={`px-3 py-1 text-xs rounded ${
                  active ? `bg-neutral-700 text-neutral-100 ring-1 ${ringColor}` : "bg-neutral-900 text-neutral-400 hover:text-neutral-200"
                }`}
              >
                {labels[f]}
              </button>
            );
          })}
        </div>

        {classes.length > 0 && (
          <div className="mt-2 flex gap-1 items-center flex-wrap">
            <span className="text-[10px] uppercase tracking-wider text-neutral-600 mr-1">Classes</span>
            {classes.slice(0, 24).map((c) => {
              const active = classFilter.includes(c.id);
              return (
                <button
                  key={c.id}
                  onClick={() => toggleClassFilter(c.id)}
                  className={`px-2 py-0.5 text-[11px] rounded border ${
                    active ? "border-emerald-500 text-neutral-100" : "border-neutral-800 text-neutral-400 hover:border-neutral-600"
                  }`}
                  style={active ? { boxShadow: `inset 0 0 0 9999px ${c.color}22` } : undefined}
                >
                  <span className="inline-block w-2 h-2 rounded-sm mr-1" style={{ background: c.color }} />
                  {c.name}
                </button>
              );
            })}
            {classFilter.length > 0 && (
              <button
                onClick={() => setSearch((sp) => { const n = new URLSearchParams(sp); n.delete("class"); return n; }, { replace: true })}
                className="text-[11px] text-neutral-500 hover:text-neutral-200 ml-1"
              >
                clear classes
              </button>
            )}
          </div>
        )}
      </div>

      {selected.size > 0 && (
        <div className="sticky top-0 z-20 bg-neutral-950/95 backdrop-blur border-b border-emerald-500/40 px-8 py-2 flex items-center gap-3 flex-wrap">
          <span className="text-sm text-emerald-400 font-medium">{selected.size} selected</span>
          <button
            onClick={() => {
              setSelected((prev) => {
                if (allVisibleSelected) {
                  const next = new Set(prev);
                  visibleIds.forEach((id) => next.delete(id));
                  return next;
                }
                const next = new Set(prev);
                visibleIds.forEach((id) => next.add(id));
                return next;
              });
            }}
            className="text-xs text-neutral-300 hover:text-white px-2 py-1 border border-neutral-700 rounded"
          >
            {allVisibleSelected ? "Deselect visible" : "Select all visible"}
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="text-xs text-neutral-400 hover:text-white"
          >
            Clear
          </button>
          <span className="mx-2 h-4 w-px bg-neutral-800" />
          <button onClick={() => bulkReview("reset")} disabled={bulkReviewBusy} className="text-xs px-3 py-1 rounded bg-neutral-700 text-neutral-200 hover:bg-neutral-600 disabled:opacity-50 border border-neutral-600">Reset to new</button>
          <button onClick={() => bulkReview("reopen")} disabled={bulkReviewBusy} className="text-xs px-3 py-1 rounded bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 disabled:opacity-50 border border-blue-500/40">Reopen</button>
          <button onClick={() => setBulkRejectOpen(true)} disabled={bulkReviewBusy} className="text-xs px-3 py-1 rounded bg-rose-500/20 text-rose-300 hover:bg-rose-500/30 disabled:opacity-50 border border-rose-500/40">Reject…</button>
          <span className="mx-2 h-4 w-px bg-neutral-800" />
          <select
            defaultValue=""
            onChange={(e) => { const v = e.target.value; e.currentTarget.selectedIndex = 0; if (v) bulkMove(v); }}
            className="text-xs bg-neutral-900 border border-neutral-700 text-neutral-200 rounded px-2 py-1"
            title="Move selected to another dataset (approved images blocked)"
          >
            <option value="">Move to dataset…</option>
            {allDatasets.filter((d) => d.id !== datasetId).map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
          {classes.length > 0 && (
            <select
              defaultValue=""
              onChange={(e) => { const v = e.target.value; e.currentTarget.selectedIndex = 0; if (v) bulkDeleteAnnotationsByClass(v); }}
              className="text-xs bg-neutral-900 border border-neutral-700 text-neutral-200 rounded px-2 py-1"
              title="Delete all annotations of a given class on selected images (locked images skipped)"
            >
              <option value="">Clear class…</option>
              {classes.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </select>
          )}
          <span className="mx-2 h-4 w-px bg-neutral-800" />
          <button onClick={bulkDelete} className="text-xs px-3 py-1 rounded bg-rose-600 text-white hover:bg-rose-500">Delete images</button>
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="p-16 text-center text-neutral-500 border-2 border-dashed border-neutral-800 m-8 rounded-lg">
          {images.length === 0 && reviewFilter === "all" && hasAnnotations === undefined && classFilter.length === 0
            ? "Drop images here, or click Upload images above. We'll pre-compute SAM2 embeddings in the background — click-to-segment is instant once an image is Ready."
            : `No images match the current filters.`}
        </div>
      ) : (
        <div className="p-8">
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
            {filtered.map((img) => (
              <ImageCard
                key={img.id}
                img={img}
                projectId={projectId}
                datasetId={datasetId}
                selected={selected.has(img.id)}
                anySelected={selected.size > 0}
                onToggle={(ev) => toggleOne(img.id, ev)}
              />
            ))}
          </div>
          {images.length < total && (
            <div className="mt-6 flex justify-center">
              <button
                onClick={loadMore}
                disabled={loadingMore}
                className="bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-100 px-5 py-2 rounded text-sm border border-neutral-700"
              >
                {loadingMore ? "Loading…" : `Load more (${images.length} of ${total})`}
              </button>
            </div>
          )}
          {images.length >= total && total > PAGE && (
            <div className="mt-6 text-center text-xs text-neutral-500">
              Showing all {total} images
            </div>
          )}
        </div>
      )}
      </div>
      <RejectReasonDialog
        open={bulkRejectOpen}
        title={`Reject ${selected.size} selected image${selected.size === 1 ? "" : "s"}`}
        description="Add a reason that applies to every selected image."
        busy={bulkReviewBusy}
        onCancel={() => setBulkRejectOpen(false)}
        onConfirm={(reason) => bulkReview("reject", reason)}
      />
    </>
  );
}

function ImageCard({
  img, projectId, datasetId, selected, anySelected, onToggle,
}: {
  img: ImageRow; projectId: string; datasetId: string;
  selected: boolean; anySelected: boolean;
  onToggle: (ev: React.MouseEvent) => void;
}) {
  const statusColor = {
    uploaded: "bg-amber-500",
    embedding: "bg-amber-500 animate-pulse",
    ready: "bg-emerald-500",
    failed: "bg-rose-500",
  }[img.status];

  const reviewDot: Record<ReviewStatus, string> = {
    new: "bg-neutral-500",
    in_progress: "bg-blue-400",
    submitted: "bg-amber-400",
    approved: "bg-emerald-400",
    rejected: "bg-rose-400",
  };

  // When ANY image is selected, clicking a card toggles selection rather than
  // navigating to the workview — gives a fluid bulk-edit feel.
  const handleCardClick = (e: React.MouseEvent) => {
    if (anySelected) {
      e.preventDefault();
      onToggle(e);
    }
  };

  return (
    <Link
      to={`/projects/${projectId}/datasets/${datasetId}/workview/${img.id}`}
      onClick={handleCardClick}
      className={`block bg-neutral-900 border ${selected ? "border-emerald-500 ring-1 ring-emerald-500" : "border-neutral-800 hover:border-emerald-500"} rounded overflow-hidden group relative`}
    >
      <button
        type="button"
        onClick={(e) => { e.preventDefault(); e.stopPropagation(); onToggle(e); }}
        className={`absolute top-2 right-7 z-10 w-4 h-4 rounded border ${selected ? "bg-emerald-500 border-emerald-400 text-neutral-950" : "bg-neutral-950/70 border-neutral-600 text-transparent hover:text-neutral-300 opacity-0 group-hover:opacity-100"} flex items-center justify-center text-[10px] font-bold ${selected ? "opacity-100" : ""}`}
        title="Select for bulk action (shift-click for range)"
      >
        ✓
      </button>
      <div className="relative aspect-square bg-neutral-950 overflow-hidden">
        {img.thumbnail_url ? (
          <img src={fileUrl(img.thumbnail_url)} alt={img.filename}
               loading="lazy" decoding="async"
               className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full grid place-items-center text-neutral-700 text-xs">no preview</div>
        )}
        <div className={`absolute top-2 right-2 w-2.5 h-2.5 rounded-full ${statusColor}`} title={img.status} />
        <div
          className={`absolute top-2 left-2 w-2.5 h-2.5 rounded-full ${reviewDot[img.review_status]}`}
          title={`Review: ${img.review_status.replace("_", " ")}${img.review_reason ? ` — ${img.review_reason}` : ""}`}
        />
        {img.open_comment_count > 0 && (
          <div
            className="absolute top-2 left-7 bg-pink-600 text-white text-[10px] px-1.5 py-0.5 rounded-full font-medium"
            title={`${img.open_comment_count} open comment(s)`}
          >💬 {img.open_comment_count}</div>
        )}
        {img.annotation_count > 0 && (
          <div className="absolute bottom-2 left-2 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded">
            {img.annotation_count}
          </div>
        )}
        {img.annotation_count === 0 && img.uncertainty != null && (
          <div
            className="absolute bottom-2 right-2 bg-black/70 text-[10px] px-1.5 py-0.5 rounded font-mono"
            style={{ color: img.uncertainty > 0.4 ? "#fca5a5" : img.uncertainty > 0.15 ? "#fde68a" : "#86efac" }}
            title={`Uncertainty score (1 - mean confidence over a 3x3 prompt grid)`}
          >
            ?{(img.uncertainty * 100).toFixed(0)}
          </div>
        )}
      </div>
      <div className="px-2 py-1.5 text-[11px] truncate text-neutral-400">{img.filename}</div>
    </Link>
  );
}
