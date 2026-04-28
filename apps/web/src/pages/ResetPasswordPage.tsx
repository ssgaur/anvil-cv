import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setErr(null);
    if (!token) { setErr("Missing token."); return; }
    if (password !== confirm) { setErr("Passwords don't match."); return; }
    if (password.length < 4) { setErr("Password must be at least 4 characters."); return; }
    setBusy(true);
    try {
      await api.passwordResetConfirm(token, password);
      setOk(true);
      setTimeout(() => nav("/login", { replace: true }), 1500);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Reset failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-950 text-neutral-100">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 p-6 bg-neutral-900 rounded">
        <h1 className="text-xl font-semibold">Choose a new password</h1>
        {ok ? (
          <div className="text-sm text-emerald-400">Password updated. Redirecting to sign in…</div>
        ) : (
          <>
            <label className="block">
              <span className="text-sm text-neutral-400">New password</span>
              <input
                type="password" required value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded bg-neutral-800 px-3 py-2 text-sm"
              />
            </label>
            <label className="block">
              <span className="text-sm text-neutral-400">Confirm</span>
              <input
                type="password" required value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="mt-1 w-full rounded bg-neutral-800 px-3 py-2 text-sm"
              />
            </label>
            {err && <div className="text-sm text-rose-400">{err}</div>}
            <button
              type="submit" disabled={busy}
              className="w-full rounded bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
            >
              {busy ? "Updating…" : "Update password"}
            </button>
            <Link to="/login" className="block text-sm text-neutral-400 hover:underline text-center">
              Back to sign in
            </Link>
          </>
        )}
      </form>
    </div>
  );
}
