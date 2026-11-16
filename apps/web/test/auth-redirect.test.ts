import { describe, expect, it } from "vitest";

import { loginUrl, safeNext } from "@/lib/auth-redirect";

describe("loginUrl", () => {
  it("builds a next param for a protected page", () => {
    expect(loginUrl("/projects/pid-1/editor", "?connection=abc")).toBe("/login?next=%2Fprojects%2Fpid-1%2Feditor%3Fconnection%3Dabc");
  });
  it("never loops back to /login itself", () => {
    expect(loginUrl("/login")).toBe("/login");
  });
  it("adds the expired reason when given", () => {
    expect(loginUrl("/projects", "", "expired")).toBe("/login?next=%2Fprojects&reason=expired");
  });
});

describe("safeNext", () => {
  it("accepts a plain same-origin path", () => {
    expect(safeNext("/projects/pid-1/editor")).toBe("/projects/pid-1/editor");
  });
  it("falls back home for a missing or relative value", () => {
    expect(safeNext(null)).toBe("/projects");
    expect(safeNext(undefined)).toBe("/projects");
    expect(safeNext("not-a-path")).toBe("/projects");
  });
  it("rejects a protocol-relative or backslash trick that browsers resolve to another host", () => {
    expect(safeNext("//evil.com")).toBe("/projects");
    expect(safeNext("/\\evil.com")).toBe("/projects");
  });
  it("never sends someone back to /login", () => {
    expect(safeNext("/login")).toBe("/projects");
    expect(safeNext("/login?next=/projects")).toBe("/projects");
  });
  it("keeps the search and hash of an accepted path", () => {
    expect(safeNext("/projects/pid-1/editor?connection=abc#top")).toBe("/projects/pid-1/editor?connection=abc#top");
  });
});
