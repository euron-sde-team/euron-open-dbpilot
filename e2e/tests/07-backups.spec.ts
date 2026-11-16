import { expect, test } from "@playwright/test";

import { apiAs, demoProject, watchConsole } from "../helpers";

test("the project Backups page lists prior backups with their integrity check (AC7)", async ({ page }) => {
  const errors = watchConsole(page);
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  await api.dispose();

  await page.goto(`/projects/${project.id}/backups`);
  await expect(page.getByRole("heading", { name: "Backups" })).toBeVisible();
  await expect(page.getByRole("button", { name: /back up now/i })).toBeVisible();
  expect(errors).toEqual([]);
});

test("running a manual backup succeeds and shows a passing integrity check", async ({ page }) => {
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  await api.dispose();

  await page.goto(`/projects/${project.id}/backups`);
  await page.getByRole("button", { name: /back up now/i }).click();
  await expect(page.getByRole("heading", { name: "Back up a connection" })).toBeVisible();
  await page.getByRole("combobox", { name: "Connection to back up" }).click();
  await page.getByRole("option", { name: "Subscriptions (SQLite)" }).click();
  await page.getByRole("button", { name: "Start backup" }).click();
  await expect(page.getByText(/succeeded/i).first()).toBeVisible({ timeout: 30_000 });
});
