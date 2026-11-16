import { describe, expect, it } from "vitest";

import { can, isInstanceAdmin, isProjectRole, isRole, projectPermissions, roleLabel } from "@/lib/roles";

describe("isRole/roleLabel", () => {
  it("recognizes the two instance roles", () => {
    expect(isRole("admin")).toBe(true);
    expect(isRole("user")).toBe(true);
    expect(isRole("owner")).toBe(false);
  });

  it("labels a known role and falls back for an unknown one", () => {
    expect(roleLabel("admin")).toBe("Admin");
    expect(roleLabel("bogus")).toBe("Unknown");
    expect(roleLabel(null)).toBe("Unknown");
  });
});

describe("isInstanceAdmin", () => {
  it("is true only for the admin role", () => {
    expect(isInstanceAdmin("admin")).toBe(true);
    expect(isInstanceAdmin("user")).toBe(false);
    expect(isInstanceAdmin(null)).toBe(false);
  });
});

describe("isProjectRole", () => {
  it("recognizes viewer, admin and owner", () => {
    expect(isProjectRole("viewer")).toBe(true);
    expect(isProjectRole("admin")).toBe(true);
    expect(isProjectRole("owner")).toBe(true);
    expect(isProjectRole("editor")).toBe(false);
  });
});

describe("can (project role rank)", () => {
  it("ranks owner above admin above viewer", () => {
    expect(can("owner", "admin")).toBe(true);
    expect(can("owner", "owner")).toBe(true);
    expect(can("admin", "owner")).toBe(false);
    expect(can("admin", "viewer")).toBe(true);
    expect(can("viewer", "admin")).toBe(false);
  });

  it("treats a missing or unrecognized role as below every requirement", () => {
    expect(can(null, "viewer")).toBe(false);
    expect(can(undefined, "viewer")).toBe(false);
    expect(can("bogus", "viewer")).toBe(false);
  });
});

describe("projectPermissions", () => {
  it("gives a viewer only the ability to view: no write-adjacent control shows", () => {
    const p = projectPermissions("viewer");
    expect(p).toEqual({ canView: true, canManageConnections: false, canConfirmWrites: false, canManageMembers: false });
  });

  it("gives an admin connection management and write confirmation, but not member management", () => {
    const p = projectPermissions("admin");
    expect(p.canManageConnections).toBe(true);
    expect(p.canConfirmWrites).toBe(true);
    expect(p.canManageMembers).toBe(false);
  });

  it("gives an owner every permission", () => {
    const p = projectPermissions("owner");
    expect(p).toEqual({ canView: true, canManageConnections: true, canConfirmWrites: true, canManageMembers: true });
  });

  it("gives a signed-out or unknown role nothing", () => {
    const p = projectPermissions(null);
    expect(p).toEqual({ canView: false, canManageConnections: false, canConfirmWrites: false, canManageMembers: false });
  });
});
