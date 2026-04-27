import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../store/auth";

export function SetupPage() {
  const navigate = useNavigate();
  const me = useAuth((s) => s.user);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [statusErr, setStatusErr] = useState<string | null>(null);

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.setupStatus()
      .then((s) => setNeedsSetup(s.needs_setup))
      .catch((e) => setStatusErr(e instanceof Error ? e.message : "status failed"));
  }, []);

  if (statusErr) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-neutral-950 text-neutral-100 p-8">
        <div className="max-w-md text-center">
          <div className="text-rose-400 mb-2">Cannot reach the API.</div>
          <div className="text-sm text-neutral-500">{statusErr}</div>
        </div>
      </div>
    );
  }
  if (needsSetup === null) {
    return <div className="p-8 text-neutral-500">checking instance…</div>;
  }
  if (!needsSetup) {
    // Already initialized — bounce to login (or projects if signed in).
    return <Navigate to={me ? "/projects" : "/login"} replace />;
  }

  const pwOk = password.length >= 8;
  const canSubmit = email.includes("@") && name.trim().length >= 1 && pwOk && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setErr(null);
    try {
      await api.setupInitialize({ email: email.trim().toLowerCase(), name: name.trim(), password });
      // Token is already stored by api.setupInitialize. Hydrate auth + go.
      await useAuth.getState().hydrate();
      navigate("/projects", { replace: true });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "setup failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 flex items-center justify-center p-8">
      <div className="w-full max-w-md bg-neutral-900 border border-neutral-800 rounded-lg p-6">
        <div className="mb-4">
          <h1 className="text-xl font-semibold">⚒ Welcome to Anvil</h1>
          <p className="text-sm text-neutral-500 mt-1">
            Create the first administrator. This account can manage users and projects.
            You'll only see this screen once.
          </p>
        </div>

        <form onSubmit={submit} className="space-y-3">
          <input
            type="email" required autoFocus
            placeholder="admin email"
            className="w-full bg-neutral-950 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
            value={email} onChange={(e) => setEmail(e.target.value)}
          />
          <input
            type="text" required
            placeholder="display name"
            className="w-full bg-neutral-950 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
            value={name} onChange={(e) => setName(e.target.value)}
          />
          <div>
            <input
              type="password" required
              placeholder="password (min 8 chars)"
              className="w-full bg-neutral-950 border border-neutral-800 rounded px-3 py-2 text-sm focus:border-emerald-500 outline-none"
              value={password} onChange={(e) => setPassword(e.target.value)}
            />
            {password && !pwOk && (
              <div className="text-xs text-amber-400 mt-1">password must be at least 8 characters</div>
            )}
          </div>

          {err && <div className="text-sm text-rose-400">{err}</div>}

          <button
            type="submit" disabled={!canSubmit}
            className="w-full bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 font-medium py-2 rounded text-sm"
          >
            {busy ? "creating…" : "Create administrator"}
          </button>
        </form>

        <div className="mt-4 text-[11px] text-neutral-600">
          After this, additional users can be invited from <b>Users</b> in the sidebar.
        </div>
      </div>
    </div>
  );
}
