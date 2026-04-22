import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../store/auth";

export function ProtectedRoute() {
  const { user, loading, hydrate } = useAuth();
  const loc = useLocation();

  useEffect(() => { hydrate(); }, [hydrate]);

  if (loading) return <div className="p-8 text-neutral-500">loading…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: loc }} />;
  return <Outlet />;
}
