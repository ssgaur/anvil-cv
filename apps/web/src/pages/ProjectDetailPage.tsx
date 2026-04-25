import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import type { ClassProperty, ClassPropertyType, ClassRow, Dataset, ModelVersion, Project } from "../types";

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
                  ? "border-emerald-500 text-neutral-100"
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
          <ModelsTab projectId={projectId} datasets={datasets} project={project} onReload={reload} />
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
  // Mirror of API curated palette — high-contrast against neutral-950
  "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16", "#22c55e",
  "#10b981", "#14b8a6", "#06b6d4", "#0ea5e9", "#3b82f6", "#6366f1",
  "#8b5cf6", "#a855f7", "#d946ef", "#ec4899", "#f43f5e", "#fb7185",
  "#facc15", "#22d3ee", "#34d399", "#a78bfa", "#fbbf24", "#fca5a5",
];

function ClassesTab({ projectId, classes, onReload }: { projectId: string; classes: ClassRow[]; onReload: () => void }) {
  const [name, setName] = useState("");
  const [color, setColor] = useState(PALETTE[5]);  // default green
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  async function create(e: FormEvent) {
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
    <div className="max-w-3xl">
      <form onSubmit={create} className="flex gap-2 mb-6 items-center flex-wrap">
        <input
          className="flex-1 min-w-[200px] bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
          placeholder="Class name (e.g. cucumber, sunkenArea)"
          value={name} onChange={(e) => setName(e.target.value)}
        />
        <PaletteRow color={color} onPick={setColor} />
        <button disabled={busy || !name.trim()} className="bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 px-4 py-2 rounded text-sm font-medium">
          Add class
        </button>
      </form>

      {classes.length === 0 ? (
        <div className="text-neutral-500 text-sm">No classes yet. Add one before annotating.</div>
      ) : (
        <ul className="space-y-2">
          {classes.map((c) => (
            <ClassCard
              key={c.id}
              projectId={projectId}
              cls={c}
              expanded={editingId === c.id}
              onExpand={() => setEditingId(editingId === c.id ? null : c.id)}
              onDelete={() => del(c)}
              onChanged={onReload}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function PaletteRow({ color, onPick }: { color: string; onPick: (c: string) => void }) {
  return (
    <div className="flex flex-wrap gap-1 max-w-[280px]">
      {PALETTE.map((c) => (
        <button
          type="button" key={c}
          onClick={() => onPick(c)}
          title={c}
          className={`w-5 h-5 rounded ${color === c ? "ring-2 ring-white ring-offset-2 ring-offset-neutral-950" : ""}`}
          style={{ background: c }}
        />
      ))}
    </div>
  );
}

function ClassCard({
  projectId, cls, expanded, onExpand, onDelete, onChanged,
}: {
  projectId: string; cls: ClassRow; expanded: boolean;
  onExpand: () => void; onDelete: () => void; onChanged: () => void;
}) {
  const [name, setName] = useState(cls.name);
  const [color, setColor] = useState(cls.color);
  const [description, setDescription] = useState(cls.description ?? "");
  const [savingMeta, setSavingMeta] = useState(false);
  const [props, setProps] = useState<ClassProperty[] | null>(null);
  const [propsErr, setPropsErr] = useState<string | null>(null);
  const dirty = name !== cls.name || color !== cls.color || description !== (cls.description ?? "");

  useEffect(() => {
    if (!expanded || props !== null) return;
    api.listClassProperties(projectId, cls.id)
      .then(setProps).catch((e) => setPropsErr(e instanceof Error ? e.message : "failed"));
  }, [expanded, projectId, cls.id, props]);

  async function saveMeta() {
    setSavingMeta(true);
    try {
      await api.patchClass(projectId, cls.id, {
        name: name.trim() || cls.name, color, description,
      });
      onChanged();
    } finally { setSavingMeta(false); }
  }

  async function reloadProps() {
    setProps(await api.listClassProperties(projectId, cls.id));
  }

  return (
    <li className="border border-neutral-800 rounded bg-neutral-950/40">
      <button
        onClick={onExpand}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-neutral-900/40 text-left"
      >
        <span className="w-4 h-4 rounded shrink-0" style={{ background: cls.color }} />
        <span className="font-medium">{cls.name}</span>
        <span className="text-xs text-neutral-500">{cls.geometry_type}</span>
        <span className="ml-auto text-xs text-neutral-400">{cls.annotation_count} ann · {expanded ? "▼" : "▶"}</span>
      </button>
      {expanded && (
        <div className="border-t border-neutral-800 p-4 space-y-4">
          {/* Meta */}
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[200px]">
              <label className="text-xs text-neutral-500 block mb-1">Name</label>
              <input
                className="w-full bg-neutral-900 border border-neutral-800 rounded px-3 py-1.5 text-sm focus:border-emerald-500 outline-none"
                value={name} onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs text-neutral-500 block mb-1">Color</label>
              <PaletteRow color={color} onPick={setColor} />
            </div>
          </div>
          <div>
            <label className="text-xs text-neutral-500 block mb-1">Description</label>
            <textarea
              className="w-full bg-neutral-900 border border-neutral-800 rounded px-3 py-1.5 text-sm focus:border-emerald-500 outline-none"
              rows={2}
              value={description} onChange={(e) => setDescription(e.target.value)}
              placeholder="(optional) what counts / doesn't count for this class"
            />
          </div>
          <div className="flex gap-2">
            <button
              disabled={!dirty || savingMeta}
              onClick={saveMeta}
              className="bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 px-3 py-1.5 rounded text-sm font-medium"
            >
              {savingMeta ? "Saving…" : "Save changes"}
            </button>
            <button onClick={onDelete} className="ml-auto text-rose-400 hover:text-rose-300 text-sm">
              Delete class
            </button>
          </div>

          {/* Properties */}
          <div className="pt-3 border-t border-neutral-800">
            <div className="text-xs uppercase tracking-wide text-neutral-500 mb-2">
              Properties (per-annotation attributes)
            </div>
            {propsErr && <div className="text-xs text-rose-400 mb-2">{propsErr}</div>}
            {props === null ? (
              <div className="text-xs text-neutral-500">loading…</div>
            ) : (
              <ClassPropertyEditor
                projectId={projectId} classId={cls.id} properties={props}
                onChanged={reloadProps}
              />
            )}
          </div>
        </div>
      )}
    </li>
  );
}

function ClassPropertyEditor({
  projectId, classId, properties, onChanged,
}: {
  projectId: string; classId: string; properties: ClassProperty[];
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState<ClassPropertyType>("single");
  const [optsText, setOptsText] = useState("");
  const [required, setRequired] = useState(false);

  async function add() {
    if (!name.trim()) return;
    const options = (type === "single" || type === "multi")
      ? optsText.split(",").map((s) => s.trim()).filter(Boolean)
      : [];
    if ((type === "single" || type === "multi") && options.length === 0) {
      alert("Add at least one option (comma-separated) for single/multi properties.");
      return;
    }
    setAdding(true);
    try {
      await api.createClassProperty(projectId, classId, {
        name: name.trim(), type, options, required,
        sort_order: properties.length,
      });
      setName(""); setOptsText(""); setRequired(false);
      onChanged();
    } finally { setAdding(false); }
  }

  return (
    <div className="space-y-2">
      {properties.length === 0 ? (
        <div className="text-xs text-neutral-500">No properties yet. Add one to capture per-annotation attributes (e.g. <code>severity = mild | moderate | severe</code>).</div>
      ) : (
        <ul className="divide-y divide-neutral-800 border border-neutral-800 rounded">
          {properties.map((p) => (
            <ClassPropertyRow key={p.id} projectId={projectId} classId={classId} property={p} onChanged={onChanged} />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-end gap-2 pt-1">
        <div className="flex-1 min-w-[140px]">
          <label className="text-xs text-neutral-500 block mb-0.5">Property name</label>
          <input
            className="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1 text-sm focus:border-emerald-500 outline-none"
            placeholder="e.g. severity"
            value={name} onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs text-neutral-500 block mb-0.5">Type</label>
          <select
            className="bg-neutral-900 border border-neutral-800 rounded px-2 py-1 text-sm"
            value={type} onChange={(e) => setType(e.target.value as ClassPropertyType)}
          >
            <option value="single">Single (radio)</option>
            <option value="multi">Multi (checkboxes)</option>
            <option value="text">Text</option>
          </select>
        </div>
        {(type === "single" || type === "multi") && (
          <div className="flex-1 min-w-[180px]">
            <label className="text-xs text-neutral-500 block mb-0.5">Options (comma-separated)</label>
            <input
              className="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1 text-sm focus:border-emerald-500 outline-none"
              placeholder="e.g. mild, moderate, severe"
              value={optsText} onChange={(e) => setOptsText(e.target.value)}
            />
          </div>
        )}
        <label className="flex items-center gap-1.5 text-xs text-neutral-400 pb-1">
          <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
          Required
        </label>
        <button
          disabled={adding || !name.trim()}
          onClick={add}
          className="bg-neutral-800 hover:bg-neutral-700 disabled:opacity-50 border border-neutral-700 text-neutral-100 px-3 py-1 rounded text-sm"
        >
          + Add property
        </button>
      </div>
    </div>
  );
}

function ClassPropertyRow({
  projectId, classId, property, onChanged,
}: {
  projectId: string; classId: string; property: ClassProperty; onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(property.name);
  const [optsText, setOptsText] = useState(property.options.join(", "));
  const [required, setRequired] = useState(property.required);

  async function save() {
    const options = (property.type === "single" || property.type === "multi")
      ? optsText.split(",").map((s) => s.trim()).filter(Boolean)
      : [];
    await api.patchClassProperty(projectId, classId, property.id, {
      name: name.trim() || property.name,
      options, required,
    });
    setEditing(false);
    onChanged();
  }

  async function del() {
    if (!confirm(`Delete property "${property.name}"? Existing values on annotations will be cleared.`)) return;
    await api.deleteClassProperty(projectId, classId, property.id);
    onChanged();
  }

  if (editing) {
    return (
      <li className="px-3 py-2 space-y-1.5 bg-neutral-900/40">
        <div className="flex flex-wrap gap-2 items-center">
          <input
            className="flex-1 min-w-[120px] bg-neutral-900 border border-neutral-800 rounded px-2 py-1 text-sm"
            value={name} onChange={(e) => setName(e.target.value)}
          />
          <span className="text-xs text-neutral-500">{property.type}</span>
          <label className="flex items-center gap-1 text-xs text-neutral-400">
            <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
            required
          </label>
        </div>
        {(property.type === "single" || property.type === "multi") && (
          <input
            className="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1 text-sm"
            placeholder="options, comma-separated"
            value={optsText} onChange={(e) => setOptsText(e.target.value)}
          />
        )}
        <div className="flex gap-2">
          <button onClick={save} className="text-emerald-400 text-xs">Save</button>
          <button onClick={() => setEditing(false)} className="text-neutral-500 text-xs">Cancel</button>
        </div>
      </li>
    );
  }

  return (
    <li className="px-3 py-2 flex items-center gap-3 group">
      <span className="font-medium text-sm">{property.name}</span>
      <span className="text-xs text-neutral-500">{property.type}</span>
      {property.required && <span className="text-xs text-amber-400">required</span>}
      {property.options.length > 0 && (
        <span className="text-xs text-neutral-500 truncate">{property.options.join(" · ")}</span>
      )}
      <button onClick={() => setEditing(true)} className="ml-auto text-neutral-500 hover:text-neutral-300 text-xs opacity-0 group-hover:opacity-100">edit</button>
      <button onClick={del} className="text-neutral-600 hover:text-rose-400 text-xs opacity-0 group-hover:opacity-100">✕</button>
    </li>
  );
}

function ModelsTab({
  projectId, datasets, project, onReload,
}: { projectId: string; datasets: Dataset[]; project: Project; onReload: () => void }) {
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [datasetId, setDatasetId] = useState<string>(datasets[0]?.id ?? "");
  const [epochs, setEpochs] = useState(20);
  const [rank, setRank] = useState(8);
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try { setVersions(await api.listModelVersions(projectId)); }
    catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [projectId]);

  // Poll while any version is training
  useEffect(() => {
    const pending = versions.some((v) => v.status === "pending" || v.status === "training");
    if (!pending) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versions]);

  useEffect(() => { if (!datasetId && datasets[0]) setDatasetId(datasets[0].id); }, [datasets, datasetId]);

  async function startTraining(e: FormEvent) {
    e.preventDefault();
    setCreating(true); setErr(null);
    try {
      await api.createModelVersion(projectId, {
        name: name.trim() || `v${versions.length + 1}`,
        dataset_id: datasetId,
        epochs, rank, alpha: rank * 2,
      });
      setName(""); setShowForm(false);
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "failed to start"); }
    finally { setCreating(false); }
  }

  async function setActive(v: ModelVersion | null) {
    await api.setActiveModel(projectId, v?.id ?? null);
    await onReload();
    await load();
  }

  return (
    <div className="max-w-4xl">
      <div className="flex items-end justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold">Models</h2>
          <p className="text-xs text-neutral-500 max-w-xl">
            Fine-tune SAM2 on this project's annotations. The fine-tuned adapter then powers
            auto-annotate clicks for everyone in the project.
          </p>
        </div>
        <button
          onClick={() => setShowForm((s) => !s)}
          className="bg-emerald-500 hover:bg-emerald-400 text-neutral-950 font-medium px-4 py-2 rounded text-sm"
        >
          {showForm ? "Cancel" : "Train new model"}
        </button>
      </div>

      {showForm && (
        <form onSubmit={startTraining} className="bg-neutral-900 border border-neutral-800 rounded-lg p-4 mb-4 space-y-3">
          <div className="grid grid-cols-2 gap-3 text-xs">
            <Field label="Name">
              <input value={name} onChange={(e) => setName(e.target.value)}
                placeholder={`v${versions.length + 1}`}
                className="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1.5" />
            </Field>
            <Field label="Dataset">
              <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}
                className="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1.5">
                {datasets.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.image_count} imgs)</option>)}
              </select>
            </Field>
            <Field label="Epochs">
              <input type="number" min={1} max={200} value={epochs}
                onChange={(e) => setEpochs(Math.max(1, Math.min(200, +e.target.value)))}
                className="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1.5" />
            </Field>
            <Field label="LoRA rank">
              <input type="number" min={1} max={64} value={rank}
                onChange={(e) => setRank(Math.max(1, Math.min(64, +e.target.value)))}
                className="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1.5" />
            </Field>
          </div>
          {err && <div className="text-rose-400 text-xs">{err}</div>}
          <button disabled={creating || !datasetId}
            className="bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 font-medium px-4 py-2 rounded text-sm">
            {creating ? "Starting…" : "Start training"}
          </button>
        </form>
      )}

      {versions.length === 0 ? (
        <div className="text-neutral-500 text-sm">
          No fine-tuned versions yet. Click "Train new model" to fine-tune SAM2 on your annotations.
          Until then, auto-annotate uses the base SAM2 model.
        </div>
      ) : (
        <div className="space-y-2">
          {versions.map((v) => {
            const isActive = project.active_model_version_id === v.id;
            const finalIou = (v.metrics?.final_val_iou as number | undefined);
            return (
              <div key={v.id} className="bg-neutral-900 border border-neutral-800 rounded p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{v.name}</span>
                      <StatusBadge status={v.status} />
                      {isActive && (
                        <span className="text-[10px] uppercase tracking-wider bg-emerald-500/20 text-emerald-400 px-2 py-0.5 rounded">
                          active
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-neutral-500 mt-1">
                      {v.base_model} · {v.num_train_images} imgs · {v.num_train_annotations} anns
                      {finalIou !== undefined && <> · val IoU <b className="text-emerald-400">{finalIou.toFixed(3)}</b></>}
                    </div>
                    {v.error && <div className="text-xs text-rose-400 mt-1">{v.error}</div>}
                    {v.status === "training" && (
                      <div className="mt-2 h-1 bg-neutral-800 rounded overflow-hidden">
                        <div className="h-full bg-emerald-500 transition-all"
                          style={{ width: `${Math.max(5, v.progress * 100)}%` }} />
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col gap-1 items-end">
                    {v.status === "ready" && !isActive && (
                      <button onClick={() => setActive(v)}
                        className="text-xs bg-neutral-800 hover:bg-neutral-700 px-3 py-1 rounded">
                        Set active
                      </button>
                    )}
                    {isActive && (
                      <button onClick={() => setActive(null)}
                        className="text-xs text-neutral-500 hover:text-neutral-300">
                        Use base model
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-neutral-500 mb-1">{label}</span>
      {children}
    </label>
  );
}

function StatusBadge({ status }: { status: ModelVersion["status"] }) {
  const cls = {
    pending: "bg-neutral-700 text-neutral-300",
    training: "bg-amber-500/20 text-amber-400",
    ready: "bg-emerald-500/20 text-emerald-400",
    failed: "bg-rose-500/20 text-rose-400",
  }[status];
  return <span className={`text-[10px] uppercase tracking-wider px-2 py-0.5 rounded ${cls}`}>{status}</span>;
}
