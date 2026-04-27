import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { api } from "../lib/api";

/** App-wide gate that redirects to /setup when the instance has no admin yet.
 *  Fails open on transient API errors (with a banner) so a backend outage
 *  doesn't trap users in /setup forever. */
export function SetupGuard() {
  const loc = useLocation();
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [errored, setErrored] = useState(false);

  useEffect(() => {
    let mounted = true;
    api.setupStatus()
      .then((s) => { if (mounted) setNeedsSetup(s.needs_setup); })
      .catch(() => { if (mounted) { setErrored(true); setNeedsSetup(false); } });
    return () => { mounted = false; };
  }, []);

  if (needsSetup === null) {
    return <div className="min-h-screen p-8 text-neutral-500 bg-neutral-950">checking instance…</div>;
  }

  // Always allow /setup itself; redirect everything else when needs_setup.
  if (needsSetup && loc.pathname !== "/setup") {
    return <Navigate to="/setup" replace />;
  }

  return (
    <>
      {errored && (
        <div className="bg-rose-900/40 text-rose-200 text-xs px-3 py-1 text-center">
          Couldn't reach the API to verify setup status — proceeding optimistically.
        </div>
      )}
      <Outlet />
    </>
  );
}
