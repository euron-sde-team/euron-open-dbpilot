import { expect, test } from "@playwright/test";

import { apiAs, call, demoProject, watchConsole } from "../helpers";

test("the connections list shows every seeded engine with no secret anywhere on the page", async ({ page }) => {
  const errors = watchConsole(page);
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  await api.dispose();

  await page.goto(`/projects/${project.id}/connections`);
  await expect(page.getByRole("heading", { name: "Connections" })).toBeVisible();
  for (const name of ["Shop (PostgreSQL)", "People (MySQL)", "Inventory (SQL Server)", "Catalog (MongoDB)", "Sessions (Redis)", "Subscriptions (SQLite)"]) {
    await expect(page.getByText(name)).toBeVisible();
  }
  // AC1: a connection's secret is sealed server side and never returned by any API
  // response, so it can never appear on the page either.
  const html = await page.content();
  expect(html).not.toMatch(/"secret"\s*:/);
  expect(html).not.toMatch(/"password"\s*:\s*"[^"]/);
  expect(errors).toEqual([]);
});

test("opening a connection shows its real discovered schema and a weak-configuration check (AC6, AC13)", async ({ page }) => {
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const connections = await call<{ id: string; name: string }[]>(api, "GET", `/api/v1/projects/${project.id}/connections`);
  const shop = connections.find((c) => c.name === "Shop (PostgreSQL)");
  expect(shop, "run make demo first").toBeTruthy();
  await api.dispose();

  await page.goto(`/projects/${project.id}/connections/${shop!.id}`);
  await expect(page.getByRole("heading", { name: "Shop (PostgreSQL)" })).toBeVisible();
  await expect(page.getByText("Weak configuration check")).toBeVisible();
  await expect(page.getByText(/customers|orders|products/).first()).toBeVisible();
});
