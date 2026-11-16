import { describe, expect, it } from "vitest";

import { activeHref, hrefOf, NAV, projectIdFrom, visibleTo } from "@/lib/nav";

describe("projectIdFrom", () => {
  it("extracts a UUID project id from a project path", () => {
    expect(projectIdFrom("/projects/de20e8af-66c8-441e-9b14-37ecf636237d/editor")).toBe("de20e8af-66c8-441e-9b14-37ecf636237d");
  });
  it("is null for a non-project path", () => {
    expect(projectIdFrom("/projects")).toBeNull();
    expect(projectIdFrom("/login")).toBeNull();
  });
});

describe("hrefOf", () => {
  const overview = NAV[0]?.items[0];
  const connections = NAV[1]?.items[0];
  if (!overview || !connections) throw new Error("NAV fixture missing expected items");

  it("prefixes a project item with the current project id", () => {
    expect(hrefOf(connections, "pid-1")).toBe("/projects/pid-1/connections");
  });
  it("is null for a project item with no project in context", () => {
    expect(hrefOf(connections, null)).toBeNull();
  });
  it("leaves an admin item's href untouched regardless of project", () => {
    expect(hrefOf({ href: "/admin/system", label: "System", icon: overview.icon, audience: "project" }, null)).toBe("/admin/system");
  });
});

describe("visibleTo", () => {
  const settings = NAV[2]?.items.find((i) => i.href === "/settings");
  const overview = NAV[0]?.items[0];
  if (!settings || !overview) throw new Error("NAV fixture missing expected items");

  it("shows an admin item to everyone regardless of project role", () => {
    expect(visibleTo({ href: "/admin/system", label: "System", icon: overview.icon, audience: "project" }, null, null)).toBe(true);
  });
  it("hides a project item with no project in context", () => {
    expect(visibleTo(overview, null, null)).toBe(false);
  });
  it("shows a project-owner item only to an owner", () => {
    expect(visibleTo(settings, "pid-1", "owner")).toBe(true);
    expect(visibleTo(settings, "pid-1", "admin")).toBe(false);
    expect(visibleTo(settings, "pid-1", "viewer")).toBe(false);
  });
  it("shows a plain project item to any role with a project", () => {
    expect(visibleTo(overview, "pid-1", "viewer")).toBe(true);
  });
});

describe("activeHref", () => {
  it("matches the overview item at the project's bare path", () => {
    expect(activeHref("/projects/pid-1", "pid-1")).toBe("/projects/pid-1");
  });
  it("matches the longest prefix, not a shorter overlapping one", () => {
    expect(activeHref("/projects/pid-1/connections/abc", "pid-1")).toBe("/projects/pid-1/connections");
  });
  it("falls back to the overview item (the bare project path) for an unrecognized subpath", () => {
    // Overview's href is the project's bare path, so it prefix-matches any subpath no
    // other nav item claims; this is the intended "no dead end" fallback, not a bug.
    expect(activeHref("/projects/pid-1/nonexistent", "pid-1")).toBe("/projects/pid-1");
  });
  it("is null with no project in context at all", () => {
    expect(activeHref("/projects", null)).toBeNull();
  });
});
