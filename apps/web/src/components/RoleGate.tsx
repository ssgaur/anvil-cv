import type { ReactNode } from "react";
import { useAuth } from "../store/auth";
import { roleAtLeast } from "../lib/roles";
import type { UserRole } from "../types";

export function RoleGate({ min, children, fallback = null }: {
  min: UserRole;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const role = useAuth((s) => s.user?.role);
  if (!roleAtLeast(role, min)) return <>{fallback}</>;
  return <>{children}</>;
}
