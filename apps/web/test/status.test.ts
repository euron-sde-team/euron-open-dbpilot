import { describe, expect, it } from "vitest";

import {
  backupLabel,
  backupTone,
  confirmationLabel,
  confirmationTone,
  generationLabel,
  generationTone,
  isSqlEngine,
  kindLabel,
  queryLabel,
  queryTone,
  securityTone,
} from "@/lib/status";

describe("queryLabel/queryTone", () => {
  it("labels every known query status", () => {
    expect(queryLabel("succeeded")).toBe("Succeeded");
    expect(queryLabel("refused")).toBe("Refused by the guard");
    expect(queryLabel("scan_limit")).toBe("Over the scan limit");
  });

  it("falls back to the raw status for an unknown value", () => {
    expect(queryLabel("mystery")).toBe("mystery");
  });

  it("assigns danger tone to a guard refusal, success to a succeeded run", () => {
    expect(queryTone("refused")).toBe("danger");
    expect(queryTone("succeeded")).toBe("success");
    expect(queryTone("timeout")).toBe("warning");
    expect(queryTone("unknown")).toBe("neutral");
  });
});

describe("generationLabel/generationTone", () => {
  it("marks a refused AI generation as danger, matching AC4's seriousness", () => {
    expect(generationLabel("refused")).toBe("Refused");
    expect(generationTone("refused")).toBe("danger");
  });

  it("marks an accepted generation as success", () => {
    expect(generationTone("accepted")).toBe("success");
  });
});

describe("confirmationLabel/confirmationTone", () => {
  it("marks pending as a warning tone (needs attention) and executed as success", () => {
    expect(confirmationTone("pending")).toBe("warning");
    expect(confirmationTone("executed")).toBe("success");
    expect(confirmationLabel("pending")).toBe("Pending confirmation");
  });
});

describe("backupLabel/backupTone", () => {
  it("covers running, succeeded and failed", () => {
    expect(backupTone("running")).toBe("primary");
    expect(backupTone("succeeded")).toBe("success");
    expect(backupTone("failed")).toBe("danger");
  });

  it("labels every known backup status", () => {
    expect(backupLabel("running")).toBe("Running");
    expect(backupLabel("succeeded")).toBe("Succeeded");
    expect(backupLabel("failed")).toBe("Failed");
  });
});

describe("kindLabel", () => {
  it("maps every supported engine to a display name", () => {
    expect(kindLabel("postgresql")).toBe("PostgreSQL");
    expect(kindLabel("mssql")).toBe("SQL Server");
    expect(kindLabel("mongodb")).toBe("MongoDB");
  });

  it("falls back to the raw kind for an unrecognized engine", () => {
    expect(kindLabel("oracle")).toBe("oracle");
  });
});

describe("isSqlEngine", () => {
  it("is true for the four SQL-family engines", () => {
    expect(isSqlEngine("postgresql")).toBe(true);
    expect(isSqlEngine("mysql")).toBe(true);
    expect(isSqlEngine("sqlite")).toBe(true);
    expect(isSqlEngine("mssql")).toBe(true);
  });

  it("is false for mongodb and redis, which get their own query surface (AC11)", () => {
    expect(isSqlEngine("mongodb")).toBe(false);
    expect(isSqlEngine("redis")).toBe(false);
  });
});

describe("securityTone", () => {
  it("maps every weak-configuration check status to a tone", () => {
    expect(securityTone("pass")).toBe("success");
    expect(securityTone("warn")).toBe("warning");
    expect(securityTone("fail")).toBe("danger");
    expect(securityTone("info")).toBe("info");
    expect(securityTone("unknown")).toBe("neutral");
  });
});
