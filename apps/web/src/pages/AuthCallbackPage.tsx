import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../store/auth";

/**
 * OAuth landing page.
 *
 * The backend redirects here after a successful provider login with the JWT
 * in the URL fragment (e.g. `/auth/callback#token=eyJ...`). Reading from the
 * fragment keeps the token out of any HTTP request and out of server logs.
 *
 * We:
 *   1. parse the token from `location.hash`
 *   2. store it via api.setToken
 *   3. scrub the hash from history (so the token doesn't linger)
 *   4. validate by calling /me
 *   5. navigate to /projects on success, /login on failure
 */
export function AuthCallbackPage() {
  const nav = useNavigate();
  const hydrate = useAuth((s) => s.hydrate);
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    const hash = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : window.location.hash;
    const params = new URLSearchParams(hash);
    const token = params.get("token");

    // Always scrub the hash so the token doesn't sit in browser history.
    window.history.replaceState(null, "", window.location.pathname);

    if (!token) {
      setError("Missing token in OAuth callback.");
      return;
    }

    (async () => {
      api.setToken(token);
      try {
        await api.me();
        await hydrate();
        nav("/projects", { replace: true });
      } catch {
        api.setToken(null);
        setError("Token rejected by server. Please sign in again.");
      }
    })();
  }, [hydrate, nav]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-950 text-neutral-100">
      <div className="text-sm text-neutral-400">
        {error ? (
          <div className="space-y-3 text-center">
            <div className="text-rose-400">{error}</div>
            <button
              onClick={() => nav("/login", { replace: true })}
              className="text-emerald-400 hover:underline"
            >
              Back to sign in
            </button>
          </div>
        ) : (
          "Signing you in…"
        )}
      </div>
    </div>
  );
}
