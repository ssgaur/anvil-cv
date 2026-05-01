import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../lib/api";
import { toast } from "../lib/toast";
import { useAuth } from "../store/auth";
import { isAdmin } from "../lib/roles";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { AdminUser, UserRole } from "../types";

const ROLES: UserRole[] = ["admin", "reviewer", "labeler", "viewer"];

export function AdminUsersPage() {
  const me = useAuth((s) => s.user);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  // Create-user form
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<UserRole>("labeler");
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AdminUser | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function reload() {
    setLoading(true); setErr(null);
    try { setUsers(await api.adminListUsers()); }
    catch (e) { setErr(e instanceof Error ? e.message : "load failed"); }
    finally { setLoading(false); }
  }
  useEffect(() => { reload(); }, []);

  if (!isAdmin(me?.role)) return <Navigate to="/" replace />;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !name.trim()) return;
    setCreating(true); setErr(null);
    try {
      await api.adminCreateUser({
        email: email.trim().toLowerCase(),
        name: name.trim(),
        role,
        password: password.trim() || undefined,
      });
      setEmail(""); setName(""); setPassword(""); setRole("labeler");
      await reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "create failed");
    } finally { setCreating(false); }
  }

  async function changeRole(u: AdminUser, next: UserRole) {
    if (next === u.role) return;
    if (u.id === me?.id && next !== "admin") {
      toast.warn("You cannot demote yourself.");
      return;
    }
    try {
      await api.adminPatchUser(u.id, { role: next });
      toast.success(`Updated ${u.email} → ${next}`);
      await reload();
    } catch (e) {
      toast.error(e);
    }
  }

  async function del(u: AdminUser) {
    if (u.id === me?.id) { toast.warn("You cannot delete yourself."); return; }
    setDeleteTarget(u);
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.adminDeleteUser(deleteTarget.id);
      toast.success(`Deleted ${deleteTarget.email}`);
      setDeleteTarget(null);
      await reload();
    }
    catch (e) { toast.error(e); }
    finally { setDeleting(false); }
  }

  return (
    <>
      <div className="p-8 max-w-5xl mx-auto">
        <header className="mb-6">
          <h1 className="text-2xl font-semibold">Users</h1>
          <p className="text-sm text-neutral-500">
            Roles: <b>admin</b> manage users &amp; projects · <b>reviewer</b> approve work · <b>labeler</b> annotate &amp; submit · <b>viewer</b> read-only.
          </p>
        </header>

        <form onSubmit={create} className="grid grid-cols-1 md:grid-cols-5 gap-2 mb-6 bg-neutral-900 border border-neutral-800 p-3 rounded">
          <input
            className="bg-neutral-950 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
            placeholder="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          />
          <input
            className="bg-neutral-950 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
            placeholder="name" value={name} onChange={(e) => setName(e.target.value)}
          />
          <select
            className="bg-neutral-950 border border-neutral-800 rounded px-3 py-2 text-sm"
            value={role} onChange={(e) => setRole(e.target.value as UserRole)}
          >
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <input
            className="bg-neutral-950 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
            placeholder="password (optional → OAuth-only)" type="password"
            value={password} onChange={(e) => setPassword(e.target.value)}
          />
          <button
            disabled={creating || !email.trim() || !name.trim()}
            className="bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 px-4 py-2 rounded text-sm font-medium"
          >{creating ? "..." : "Create user"}</button>
        </form>

        {err && <div className="text-rose-400 text-sm mb-4">{err}</div>}

        {loading ? (
          <div className="text-neutral-500">loading…</div>
        ) : (
          <table className="w-full text-sm border border-neutral-800 rounded overflow-hidden">
            <thead className="bg-neutral-900 text-neutral-400 text-left text-xs uppercase">
              <tr>
                <th className="px-3 py-2">Email</th>
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">Role</th>
                <th className="px-3 py-2">OAuth</th>
                <th className="px-3 py-2">Created</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-neutral-800 hover:bg-neutral-900/40">
                  <td className="px-3 py-2">
                    {u.email} {u.id === me?.id && <span className="text-[10px] text-emerald-400 ml-1">you</span>}
                  </td>
                  <td className="px-3 py-2 text-neutral-300">{u.name}</td>
                  <td className="px-3 py-2">
                    <select
                      value={u.role}
                      onChange={(e) => changeRole(u, e.target.value as UserRole)}
                      className="bg-neutral-950 border border-neutral-800 rounded px-2 py-1 text-xs"
                    >
                      {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-400">
                    {u.oauth_providers?.length ? u.oauth_providers.join(", ") : "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-neutral-500">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <button
                      onClick={() => del(u)}
                      disabled={u.id === me?.id}
                      className="text-xs text-neutral-500 hover:text-rose-400 disabled:opacity-30"
                    >delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete user?"
        description={`Delete ${deleteTarget?.email ?? "this user"}? Their account access will be removed.`}
        confirmLabel="Delete user"
        tone="danger"
        busy={deleting}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={confirmDelete}
      />
    </>
  );
}
