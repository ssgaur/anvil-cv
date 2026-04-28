import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../store/auth";

/**
 * OAuth landing page.
 *
 * The backend sets an HttpOnly access cookie on its 302 response and lands
 * the browser here with no token in the URL. We hydrate the session by
 * calling /api/auth/me, which picks up the cookie automatically.
 */
export function AuthCallbackPage() {
  const nav = useNavigate();
  const hydrate = useAuth((s) => s.hydrate);
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    (async () => {
      try {
        await hydrate();
        nav("/projects", { replace: true });
      } catch {
        setError("Sign-in failed. Please try again.");
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
