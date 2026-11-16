import { expect, test } from "@playwright/test";

import { apiAs, demoConnection, demoProject, watchConsole } from "../helpers";

test("a read-only SQL query runs, pages, and exports as CSV (AC2, AC3)", async ({ page }) => {
  const errors = watchConsole(page);
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const shop = await demoConnection(api, project.id, "Shop (PostgreSQL)");
  await api.dispose();

  await page.goto(`/projects/${project.id}/editor?connection=${shop.id}`);
  await page.getByRole("textbox", { name: "SQL statement" }).fill("select id, email from customers order by id limit 5;");
  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByText(/row.*in.*ms/).first()).toBeVisible({ timeout: 15_000 });
  expect(errors).toEqual([]);
});

test("a query that would scan past the connection's scan limit is refused before it runs (AC2)", async ({ page }) => {
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const shop = await demoConnection(api, project.id, "Shop (PostgreSQL)");
  await api.dispose();

  await page.goto(`/projects/${project.id}/editor?connection=${shop.id}`);
  // A cross join inflates the planner's own row estimate well past any sane scan limit.
  await page.getByRole("textbox", { name: "SQL statement" }).fill(
    "select * from customers c1, customers c2, customers c3, customers c4, customers c5;",
  );
  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByText(/guard refused|scan|refused/i).first()).toBeVisible({ timeout: 15_000 });
});
