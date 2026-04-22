import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../store/auth";

export function LoginPage() {
  const { user, login, signup } = useAuth();
  const nav = useNavigate();
  const loc = useLocation() as { state?: { from?: { pathname?: string } } };
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("admin@anvil.dev");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("admin");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to={loc.state?.from?.pathname ?? "/projects"} replace />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await signup(email, name, password);
      nav("/projects", { replace: true });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally { setBusy(false); }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-950 text-neutral-100">
      <form onSubmit={submit} className="w-80 bg-neutral-900 p-6 rounded-lg border border-neutral-800 space-y-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">⚒ Anvil</h1>
          <p className="text-sm text-neutral-500">Open-source image annotation</p>
        </div>

        <div className="flex gap-1 rounded bg-neutral-800 p-1 text-xs">
          {(["login", "signup"] as const).map((m) => (
            <button
              type="button"
              key={m}
              className={`flex-1 py-1 rounded ${mode === m ? "bg-neutral-700 text-white" : "text-neutral-400"}`}
              onClick={() => setMode(m)}
            >
              {m === "login" ? "Sign in" : "Create account"}
            </button>
          ))}
        </div>

        <input
          className="w-full bg-neutral-800 px-3 py-2 rounded text-sm placeholder-neutral-500 border border-neutral-700 focus:border-emerald-500 outline-none"
          placeholder="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        {mode === "signup" && (
          <input
            className="w-full bg-neutral-800 px-3 py-2 rounded text-sm placeholder-neutral-500 border border-neutral-700 focus:border-emerald-500 outline-none"
            placeholder="full name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        )}
        <input
          type="password"
          className="w-full bg-neutral-800 px-3 py-2 rounded text-sm placeholder-neutral-500 border border-neutral-700 focus:border-emerald-500 outline-none"
          placeholder="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {err && <div className="text-xs text-rose-400">{err}</div>}

        <button
          disabled={busy}
          className="w-full bg-emerald-500 hover:bg-emerald-400 disabled:bg-neutral-700 text-neutral-950 font-medium py-2 rounded text-sm"
        >
          {busy ? "..." : mode === "login" ? "Sign in" : "Create account"}
        </button>

        <p className="text-[11px] text-neutral-600 text-center">
          default admin · admin@anvil.dev / admin
        </p>
      </form>
    </div>
  );
}
