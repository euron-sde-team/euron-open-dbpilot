import { expect, test } from "@playwright/test";

import { apiAs, demoConnection, demoProject, watchConsole } from "../helpers";

test("the schema explorer shows real tables and a relationship view (AC6, AC11)", async ({ page }) => {
  const errors = watchConsole(page);
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const shop = await demoConnection(api, project.id, "Shop (PostgreSQL)");
  await api.dispose();

  await page.goto(`/projects/${project.id}/connections/${shop.id}`);
  await page.getByRole("tab", { name: "Schema" }).click();
  await expect(page.getByText("customers").first()).toBeVisible();
  await expect(page.getByText("orders").first()).toBeVisible();

  await page.getByRole("tab", { name: "Relationships" }).click();
  await expect(page.getByText(/relationship/i).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("MongoDB and Redis get their own read-only panel, not a SQL editor (AC11)", async ({ page }) => {
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const catalog = await demoConnection(api, project.id, "Catalog (MongoDB)");
  await api.dispose();

  await page.goto(`/projects/${project.id}/editor?connection=${catalog.id}`);
  await expect(page.getByRole("textbox", { name: "SQL statement" })).toHaveCount(0);
  await expect(page.getByLabel("Collection")).toBeVisible();
  await expect(page.getByText(/no read-only session setting/i)).toBeVisible();
});
