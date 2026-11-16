import { defineConfig, devices } from "@playwright/test";

// One run suffix for the whole run: the teardown reads the same one.
process.env.E2E_RUN ||= Date.now().toString(36).slice(-5);

/**
 * Browser end-to-end tests against the running local stack (`make up && make demo`): the
 * web app, the API, the scheduled-backup worker, Postgres, Redis, the five demo source
 * databases, Mailpit, the mock AI provider and, on Floci, S3 and KMS.
 *
 *   pnpm test                 every suite
 *   E2E_HEADED=1 pnpm test    watch it
 *
 * Every suite that creates data uses a per-run suffix, so reruns pass on the seeded
 * stack, and the teardown deletes what the run created. The suites share one database,
 * so they run in a single worker, in file order.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  globalTeardown: "./global-teardown.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  outputDir: "test-results",
  use: {
    baseURL: process.env.WEB_URL || "http://localhost:5810",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    headless: !process.env.E2E_HEADED,
    storageState: ".auth/admin.json",
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
