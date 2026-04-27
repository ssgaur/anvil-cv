import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../store/auth";
import { isAdmin } from "../lib/roles";
import type { Project } from "../types";

export function ProjectsPage() {
  const role = useAuth((s) => s.user?.role);
  const admin = isAdmin(role);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function reload() {
    setLoading(true);
    try { setProjects(await api.listProjects()); }
    catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
    finally { setLoading(false); }
  }
  useEffect(() => { reload(); }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true); setErr(null);
    try {
      await api.createProject({ name: name.trim() });
      setName("");
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "create failed");
    } finally { setCreating(false); }
  }

  async function del(p: Project) {
    if (!confirm(`Delete "${p.name}" and all its data?`)) return;
    await api.deleteProject(p.id);
    await reload();
  }

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <header className="flex items-end justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">Projects</h1>
          <p className="text-sm text-neutral-500">A project groups datasets, classes, and models for one labeling task.</p>
        </div>
      </header>

      <form onSubmit={create} className={`flex gap-2 mb-6 ${admin ? "" : "hidden"}`}>
        <input
          className="flex-1 bg-neutral-900 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
          placeholder="New project name (e.g. Cucumber Defects)"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          disabled={creating || !name.trim()}
          className="bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 px-4 py-2 rounded text-sm font-medium"
        >
          {creating ? "..." : "Create"}
        </button>
      </form>
      {!admin && (
        <div className="text-xs text-neutral-500 mb-6">Only administrators can create or delete projects.</div>
      )}

      {err && <div className="text-rose-400 text-sm mb-4">{err}</div>}

      {loading ? (
        <div className="text-neutral-500">loading…</div>
      ) : projects.length === 0 ? (
        <div className="text-neutral-500 text-sm">No projects yet. Create your first above.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {projects.map((p) => (
            <div key={p.id} className="bg-neutral-900 border border-neutral-800 rounded-lg p-4 hover:border-neutral-700 transition group">
              <div className="flex items-start justify-between mb-2">
                <Link to={`/projects/${p.id}`} className="font-medium hover:text-emerald-400">
                  {p.name}
                </Link>
                {admin && (
                  <button
                    onClick={() => del(p)}
                    className="opacity-0 group-hover:opacity-100 text-neutral-600 hover:text-rose-400 text-xs"
                    title="delete"
                  >
                    ✕
                  </button>
                )}
              </div>
              <div className="text-xs text-neutral-500 mb-3">{p.description || <em>no description</em>}</div>
              <div className="flex gap-4 text-xs text-neutral-400">
                <span><b className="text-neutral-200">{p.dataset_count}</b> datasets</span>
                <span><b className="text-neutral-200">{p.image_count}</b> images</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
