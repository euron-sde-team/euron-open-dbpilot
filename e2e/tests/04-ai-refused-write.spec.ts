import { expect, test } from "@playwright/test";

import { apiAs, demoConnection, demoProject, watchConsole } from "../helpers";

/**
 * AC4: an AI-generated statement that is anything but one read-only query is refused,
 * unconditionally, with no override anywhere. The mock AI provider (mockai/planner.py)
 * deliberately writes a destructive statement for a prompt containing "delete", exactly
 * like a real model that was talked into one would, so the guard's refusal is exercised
 * for real rather than mocked away.
 */
test("the AI assistant refuses a generated write with no way to run it anyway (AC4)", async ({ page }) => {
  const errors = watchConsole(page);
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const shop = await demoConnection(api, project.id, "Shop (PostgreSQL)");
  await api.dispose();

  await page.goto(`/projects/${project.id}/editor?connection=${shop.id}&panel=ai`);
  await page.getByRole("textbox", { name: "Ask a question about this connection's data" }).fill("delete the ten oldest orders");
  await page.getByRole("button", { name: "Generate SQL" }).click();
  await expect(page.getByText(/refused/i).first()).toBeVisible({ timeout: 20_000 });
  // No "run anyway" / override control exists anywhere on the page for a refused generation.
  await expect(page.getByRole("button", { name: /run anyway/i })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the AI assistant generates a genuine read-only statement for an ordinary question", async ({ page }) => {
  const api = await apiAs("maya.chen@lumenlogistics.example");
  const project = await demoProject(api);
  const shop = await demoConnection(api, project.id, "Shop (PostgreSQL)");
  await api.dispose();

  await page.goto(`/projects/${project.id}/editor?connection=${shop.id}&panel=ai`);
  await page.getByRole("textbox", { name: "Ask a question about this connection's data" }).fill("how many customers are there");
  await page.getByRole("button", { name: "Generate SQL" }).click();
  await expect(page.getByText(/select/i).first()).toBeVisible({ timeout: 20_000 });
});
