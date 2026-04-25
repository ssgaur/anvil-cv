import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, downloadCocoExport, fileUrl } from "../lib/api";
import type { Dataset, ImageRow } from "../types";

export function DatasetDetailPage() {
  const { projectId = "", datasetId = "" } = useParams();
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [images, setImages] = useState<ImageRow[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [filter, setFilter] = useState<"all" | "ready" | "uploaded" | "embedding" | "failed">("all");

  const reload = useCallback(async () => {
    const [d, imgs] = await Promise.all([
      api.getDataset(projectId, datasetId),
      api.listImages(projectId, datasetId, { limit: 500 }),
    ]);
    setDataset(d); setImages(imgs);
  }, [projectId, datasetId]);

  useEffect(() => { reload(); }, [reload]);

  // Auto-poll while embeddings are in progress
  useEffect(() => {
    const pending = images.some((i) => i.status === "uploaded" || i.status === "embedding");
    if (!pending) return;
    const t = setInterval(reload, 1500);
    return () => clearInterval(t);
  }, [images, reload]);

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
    upload(e.dataTransfer.files);
  }

  const filtered = filter === "all" ? images : images.filter((i) => i.status === filter);
  const counts = {
    all: images.length,
    ready: images.filter((i) => i.status === "ready").length,
    embedding: images.filter((i) => i.status === "embedding" || i.status === "uploaded").length,
    failed: images.filter((i) => i.status === "failed").length,
  };

  if (!dataset) return <div className="p-8 text-neutral-500">loading…</div>;

  return (
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
            <button
              onClick={async () => {
                try {
                  await downloadCocoExport(projectId, datasetId, `${dataset.slug}-coco.zip`);
                } catch (e) {
                  alert(e instanceof Error ? e.message : "export failed");
                }
              }}
              disabled={counts.ready === 0}
              title="Download annotations.json (COCO format) + images as a ZIP"
              className="bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 text-neutral-100 font-medium px-4 py-2 rounded text-sm border border-neutral-700"
            >
              Export COCO
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

        <div className="mt-4 flex gap-1">
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
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="p-16 text-center text-neutral-500 border-2 border-dashed border-neutral-800 m-8 rounded-lg">
          {images.length === 0
            ? "Drop images here, or click Upload images above. We'll pre-compute SAM2 embeddings in the background — click-to-segment is instant once an image is Ready."
            : `No images match filter "${filter}".`}
        </div>
      ) : (
        <div className="p-8 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
          {filtered.map((img) => (
            <ImageCard key={img.id} img={img} projectId={projectId} datasetId={datasetId} />
          ))}
        </div>
      )}
    </div>
  );
}

function ImageCard({ img, projectId, datasetId }: { img: ImageRow; projectId: string; datasetId: string }) {
  const statusColor = {
    uploaded: "bg-amber-500",
    embedding: "bg-amber-500 animate-pulse",
    ready: "bg-emerald-500",
    failed: "bg-rose-500",
  }[img.status];

  return (
    <Link
      to={`/projects/${projectId}/datasets/${datasetId}/workview/${img.id}`}
      className="block bg-neutral-900 border border-neutral-800 hover:border-emerald-500 rounded overflow-hidden group"
    >
      <div className="relative aspect-square bg-neutral-950 overflow-hidden">
        {img.thumbnail_url ? (
          <img src={fileUrl(img.thumbnail_url)} alt={img.filename} className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full grid place-items-center text-neutral-700 text-xs">no preview</div>
        )}
        <div className={`absolute top-2 right-2 w-2.5 h-2.5 rounded-full ${statusColor}`} title={img.status} />
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
