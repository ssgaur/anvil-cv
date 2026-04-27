import type { UserRole } from "../types";

const RANK: Record<UserRole, number> = { viewer: 0, labeler: 1, reviewer: 2, admin: 3 };

export function roleAtLeast(role: UserRole | undefined | null, min: UserRole): boolean {
  if (!role) return false;
  return RANK[role] >= RANK[min];
}

export const isAdmin    = (r?: UserRole | null) => roleAtLeast(r, "admin");
export const isReviewer = (r?: UserRole | null) => roleAtLeast(r, "reviewer");
export const isLabeler  = (r?: UserRole | null) => roleAtLeast(r, "labeler");
