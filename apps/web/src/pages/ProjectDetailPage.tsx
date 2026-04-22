import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { ClassRow, Dataset, Project } from "../types";

const TABS = ["Data", "Classes", "Models", "Settings"] as const;
type Tab = (typeof TABS)[number];

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [classes, setClasses] = useState<ClassRow[]>([]);
  const [tab, setTab] = useState<Tab>("Data");
  const [err, setErr] = useState<string | null>(null);

  async function reload() {
    try {
      const [p, ds, cs] = await Promise.all([
        api.getProject(projectId),
        api.listDatasets(projectId),
        api.listClasses(projectId),
      ]);
      setProject(p); setDatasets(ds); setClasses(cs);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    }
  }
  useEffect(() => { reload(); }, [projectId]);

  if (!project) return <div className="p-8 text-neutral-500">{err ?? "loading…"}</div>;

  return (
    <div>
      {/* Header */}
      <div className="border-b border-neutral-800 px-8 pt-6">
        <div className="text-xs text-neutral-500 mb-1">
          <Link to="/projects" className="hover:text-neutral-300">Projects</Link> / {project.slug}
        </div>
        <h1 className="text-2xl font-semibold mb-4">{project.name}</h1>
        <div className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm border-b-2 -mb-px ${
                tab === t
                  ? "border-emerald-500 text-white"
                  : "border-transparent text-neutral-400 hover:text-neutral-200"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="p-8 max-w-6xl">
        {tab === "Data" && <DataTab projectId={projectId} datasets={datasets} onReload={reload} />}
        {tab === "Classes" && <ClassesTab projectId={projectId} classes={classes} onReload={reload} />}
        {tab === "Models" && (
          <div className="text-sm text-neutral-500">
            Model versions land in Phase 2. The fine-tune button will live here.
          </div>
        )}
        {tab === "Settings" && (
          <div className="text-sm text-neutral-500">Settings (rename, transfer, delete) — Phase 1.5.</div>
        )}
      </div>
    </div>
  );
}

function DataTab({ projectId, datasets, onReload }: { projectId: string; datasets: Dataset[]; onReload: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try { await api.createDataset(projectId, { name: name.trim() }); setName(""); onReload(); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <form onSubmit={create} className="flex gap-2 mb-6 max-w-lg">
        <input
          className="flex-1 bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
          placeholder="New dataset name (e.g. Batch 01)"
          value={name} onChange={(e) => setName(e.target.value)}
        />
        <button disabled={busy || !name.trim()} className="bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 px-4 py-2 rounded text-sm font-medium">
          {busy ? "..." : "Create dataset"}
        </button>
      </form>

      {datasets.length === 0 ? (
        <div className="text-neutral-500 text-sm">No datasets yet. Create one to start uploading images.</div>
      ) : (
        <div className="space-y-2">
          {datasets.map((d) => (
            <Link
              key={d.id}
              to={`/projects/${projectId}/datasets/${d.id}`}
              className="block bg-neutral-900 border border-neutral-800 hover:border-neutral-700 rounded p-4"
            >
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">{d.name}</div>
                  <div className="text-xs text-neutral-500">{d.slug} · created {new Date(d.created_at).toLocaleDateString()}</div>
                </div>
                <div className="text-xs text-neutral-400 flex gap-4">
                  <span><b className="text-neutral-200">{d.image_count}</b> images</span>
                  <span><b className="text-emerald-400">{d.ready_count}</b> ready</span>
                </div>
              </div>
              <div className="mt-3 h-1 bg-neutral-800 rounded overflow-hidden">
                <div
                  className="h-full bg-emerald-500"
                  style={{ width: d.image_count ? `${(d.ready_count / d.image_count) * 100}%` : "0%" }}
                />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

const PALETTE = [
  "#22c55e", "#3b82f6", "#ef4444", "#f59e0b", "#a855f7",
  "#06b6d4", "#ec4899", "#84cc16", "#eab308", "#14b8a6",
  "#f97316", "#8b5cf6", "#10b981", "#0ea5e9", "#f43f5e",
];

function ClassesTab({ projectId, classes, onReload }: { projectId: string; classes: ClassRow[]; onReload: () => void }) {
  const [name, setName] = useState("");
  const [color, setColor] = useState(PALETTE[0]);
  const [busy, setBusy] = useState(false);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try { await api.createClass(projectId, { name: name.trim(), color }); setName(""); onReload(); }
    finally { setBusy(false); }
  }

  async function del(c: ClassRow) {
    if (!confirm(`Delete class "${c.name}"? Existing annotations will be removed.`)) return;
    await api.deleteClass(projectId, c.id); onReload();
  }

  return (
    <div className="max-w-2xl">
      <form onSubmit={create} className="flex gap-2 mb-6 items-center">
        <input
          className="flex-1 bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
          placeholder="Class name (e.g. sunkenAreas)"
          value={name} onChange={(e) => setName(e.target.value)}
        />
        <div className="flex gap-1">
          {PALETTE.slice(0, 10).map((c) => (
            <button
              type="button" key={c}
              onClick={() => setColor(c)}
              className={`w-6 h-6 rounded ${color === c ? "ring-2 ring-white ring-offset-2 ring-offset-neutral-950" : ""}`}
              style={{ background: c }}
            />
          ))}
        </div>
        <button disabled={busy || !name.trim()} className="bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 px-4 py-2 rounded text-sm font-medium">
          Add class
        </button>
      </form>

      {classes.length === 0 ? (
        <div className="text-neutral-500 text-sm">No classes yet. Add one before annotating.</div>
      ) : (
        <ul className="divide-y divide-neutral-800 border border-neutral-800 rounded">
          {classes.map((c) => (
            <li key={c.id} className="flex items-center gap-3 px-4 py-3 group">
              <span className="w-4 h-4 rounded" style={{ background: c.color }} />
              <span className="font-medium">{c.name}</span>
              <span className="text-xs text-neutral-500">{c.geometry_type}</span>
              <span className="ml-auto text-xs text-neutral-400">{c.annotation_count} annotations</span>
              <button onClick={() => del(c)} className="text-neutral-600 hover:text-rose-400 opacity-0 group-hover:opacity-100">✕</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
