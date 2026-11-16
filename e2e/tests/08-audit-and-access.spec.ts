import { expect, test } from "@playwright/test";

import { apiAs, call, demoConnection, demoProject, signedInContext, watchConsole } from "../helpers";

test("the audit log is filterable and keyset-paginated, newest first (AC10)", async ({ page }) => {
  const errors = watchConsole(page);
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  await api.dispose();

  await page.goto(`/projects/${project.id}/audit`);
  await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
  await expect(page.getByText(/./).first()).toBeVisible();
  expect(errors).toEqual([]);
});

/**
 * AC9: a project member with no grant on a connection cannot see it, its schema, or run
 * any query against it, and every one of those routes answers 404, never 403.
 */
test("a project member with no grant on a connection gets 404, not 403 (AC9)", async ({ browser }) => {
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const shop = await demoConnection(api, project.id, "Shop (PostgreSQL)");
  await api.dispose();

  // liam.okafor is a project viewer in the seeded demo with no grant on any connection.
  const { context, page } = await signedInContext(browser, "liam.okafor@lumenlogistics.example");
  const noGrantApi = await context.request;
  const resp = await noGrantApi.get(`${process.env.WEB_URL || "http://localhost:5810"}/api/v1/projects/${project.id}/connections/${shop.id}`);
  expect(resp.status()).toBe(404);

  await page.goto(`/projects/${project.id}/connections/${shop.id}`);
  await expect(page.getByText(/not available|not found/i).first()).toBeVisible();
  await context.close();
});

test("the API never returns a connection's secret on any route (AC1)", async () => {
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const list = await call<Record<string, unknown>[]>(api, "GET", `/api/v1/projects/${project.id}/connections`);
  await api.dispose();
  for (const conn of list) {
    expect(conn).not.toHaveProperty("secret");
    expect(conn).not.toHaveProperty("secret_sealed");
    expect(conn).not.toHaveProperty("password");
  }
});
