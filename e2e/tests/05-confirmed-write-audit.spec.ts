import { expect, test } from "@playwright/test";

import { apiAs, call, demoConnection, demoProject, watchConsole } from "../helpers";

/**
 * AC5: a human-marked write goes through a genuine two-step confirmation (a one-time
 * token plus a digest of exactly what will run) and produces three real audit entries:
 * write.requested, write.confirmed, write.executed.
 */
test("marking a statement as a write, confirming it, and seeing the full audit trail (AC5, AC10)", async ({ page }) => {
  const errors = watchConsole(page);
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  // The staging connection carries a read-write login, so a confirmed write can actually
  // succeed against it without touching the primary shop database.
  const staging = await demoConnection(api, project.id, "Shop staging (PostgreSQL, read-write login)");
  await api.dispose();

  await page.goto(`/projects/${project.id}/editor?connection=${staging.id}`);
  await page.getByRole("textbox", { name: "SQL statement" }).fill("update customers set updated_at = now() where id = 1;");
  await page.getByRole("button", { name: "Mark as a write" }).click();

  await expect(page.getByRole("heading", { name: "Mark as a write" })).toBeVisible();
  await page.getByRole("button", { name: "Review this write" }).click();

  await expect(page.getByRole("heading", { name: "Confirm this write" })).toBeVisible();
  await expect(page.getByRole("dialog").getByText(/update customers/i)).toBeVisible();
  await expect(page.getByText(/Digest:/)).toBeVisible();
  await page.getByRole("button", { name: "Confirm and run" }).click();
  await expect(page.getByText(/recorded in the audit log/i)).toBeVisible({ timeout: 15_000 });

  // The audit log now carries the full three-entry trail for this write, through the one
  // shared audit code path (AC10), newest first.
  const verify = await apiAs("maya.chen@lumenlogistics.example");
  const entries = await call<{ items: { action: string }[] }>(verify, "GET", `/api/v1/projects/${project.id}/audit?limit=10`);
  await verify.dispose();
  const actions = entries.items.map((e) => e.action);
  expect(actions).toContain("write.requested");
  expect(actions).toContain("write.confirmed");
  expect(actions).toContain("write.executed");

  await page.goto(`/projects/${project.id}/audit`);
  await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
  await expect(page.getByText("write.executed").first()).toBeVisible();
  expect(errors).toEqual([]);
});
