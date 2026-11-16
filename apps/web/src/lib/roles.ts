import type { ProjectRole, Role } from "./api/types";

export const ROLES: readonly Role[] = ["admin", "user"] as const;

const ROLE_LABEL: Record<Role, string> = { admin: "Admin", user: "User" };

export function isRole(value: unknown): value is Role {
  return typeof value === "string" && (ROLES as readonly string[]).includes(value);
}

export function roleLabel(role: string | null | undefined): string {
  return isRole(role) ? ROLE_LABEL[role] : "Unknown";
}

export function isInstanceAdmin(role: string | null | undefined): boolean {
  return role === "admin";
}

export const PROJECT_ROLES: readonly ProjectRole[] = ["viewer", "admin", "owner"] as const;
const RANK: Record<ProjectRole, number> = { viewer: 0, admin: 1, owner: 2 };

export const PROJECT_ROLE_LABEL: Record<ProjectRole, string> = { viewer: "Viewer", admin: "Admin", owner: "Owner" };

export const PROJECT_ROLE_DESCRIPTION: Record<ProjectRole, string> = {
  viewer: "Browses schemas, runs read-only queries against connections they are granted, and reads results. Cannot manage connections, members or confirm writes.",
  admin: "Also creates and manages connections, grants access to teammates, confirms writes, restores and migrations, and reads the project audit log.",
  owner: "Everything an admin can do, plus renaming or deleting the project and managing its members' roles.",
};

export function isProjectRole(value: unknown): value is ProjectRole {
  return typeof value === "string" && (PROJECT_ROLES as readonly string[]).includes(value);
}

/** Whether a project role is at least ``needed``. */
export function can(role: string | null | undefined, needed: ProjectRole): boolean {
  const have = role && role in RANK ? RANK[role as ProjectRole] : -1;
  return have >= RANK[needed];
}

/** What a project role may do, as flags for the UI (viewers see no write-adjacent controls). */
export interface ProjectPermissions {
  canView: boolean;
  canManageConnections: boolean;
  canConfirmWrites: boolean;
  canManageMembers: boolean;
}

export function projectPermissions(role: string | null | undefined): ProjectPermissions {
  const admin = can(role, "admin");
  return {
    canView: can(role, "viewer"),
    canManageConnections: admin,
    canConfirmWrites: admin,
    canManageMembers: can(role, "owner"),
  };
}
