import { describe, expect, it } from "vitest";

import { clampPct, formatBytes, formatDuration, formatNumber, formatRelative, initials, plural, shortId } from "@/lib/format";

describe("formatNumber", () => {
  it("adds thousands separators", () => {
    expect(formatNumber(14923)).toBe("14,923");
  });
  it("is n/a for null, undefined or non-finite", () => {
    expect(formatNumber(null)).toBe("n/a");
    expect(formatNumber(undefined)).toBe("n/a");
    expect(formatNumber(NaN)).toBe("n/a");
  });
});

describe("formatBytes", () => {
  it("picks the right unit by size", () => {
    expect(formatBytes(500)).toBe("500 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5 MB");
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe("3 GB");
  });
  it("is n/a for a missing value", () => {
    expect(formatBytes(null)).toBe("n/a");
  });
});

describe("formatDuration", () => {
  it("shows milliseconds under a second", () => {
    expect(formatDuration(840)).toBe("840 ms");
  });
  it("shows seconds with one decimal under a minute", () => {
    expect(formatDuration(2400)).toBe("2.4 s");
  });
  it("shows minutes and seconds at a minute or more", () => {
    expect(formatDuration(190_000)).toBe("3m 10s");
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-01-01T12:00:00Z");
  it("says Never for a missing timestamp", () => {
    expect(formatRelative(null, now)).toBe("Never");
  });
  it("says just now for something seconds old", () => {
    expect(formatRelative(new Date(now.getTime() - 5_000).toISOString(), now)).toBe("just now");
  });
  it("says minutes ago for something under an hour old", () => {
    expect(formatRelative(new Date(now.getTime() - 5 * 60_000).toISOString(), now)).toBe("5m ago");
  });
  it("says hours ago for something under a day old", () => {
    expect(formatRelative(new Date(now.getTime() - 3 * 3_600_000).toISOString(), now)).toBe("3h ago");
  });
});

describe("initials", () => {
  it("takes the first letter of the first and last name", () => {
    expect(initials("Maya Chen")).toBe("MC");
  });
  it("takes one letter for a single name", () => {
    expect(initials("Maya")).toBe("M");
  });
  it("falls back to a question mark", () => {
    expect(initials(null)).toBe("?");
    expect(initials("   ")).toBe("?");
  });
});

describe("plural", () => {
  it("uses the singular noun for exactly one", () => {
    expect(plural(1, "row")).toBe("1 row");
  });
  it("uses the plural noun otherwise", () => {
    expect(plural(0, "row")).toBe("0 rows");
    expect(plural(2000, "row")).toBe("2,000 rows");
  });
  it("accepts an irregular plural", () => {
    expect(plural(2, "index", "indexes")).toBe("2 indexes");
  });
});

describe("shortId", () => {
  it("takes the first 8 characters of a UUID", () => {
    expect(shortId("de20e8af-66c8-441e-9b14-37ecf636237d")).toBe("de20e8af");
  });
  it("is n/a for a missing id", () => {
    expect(shortId(null)).toBe("n/a");
  });
});

describe("clampPct", () => {
  it("clamps a share between 0 and 100", () => {
    expect(clampPct(50, 100)).toBe(50);
    expect(clampPct(150, 100)).toBe(100);
    expect(clampPct(-10, 100)).toBe(0);
  });
  it("is 0 for a missing value", () => {
    expect(clampPct(null)).toBe(0);
  });
});
