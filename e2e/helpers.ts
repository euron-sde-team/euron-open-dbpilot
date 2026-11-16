import { type APIRequestContext, type Browser, type BrowserContext, expect, type Page, request } from "@playwright/test";

const BASE = process.env.WEB_URL || "http://localhost:5810";
export const MAILPIT = process.env.MAILPIT_URL || "http://localhost:5825";
export const PASSWORD = process.env.E2E_PASSWORD || process.env.DBPILOT_DEMO_PASSWORD || "";
export const ADMIN = { email: process.env.E2E_EMAIL || "maya.chen@lumenlogistics.example", password: PASSWORD };
export const PROJECT_ADMIN_EMAIL = "arjun.rao@lumenlogistics.example";
export const VIEWER_EMAIL = "sofia.alvarez@lumenlogistics.example";
export const NO_GRANT_VIEWER_EMAIL = "liam.okafor@lumenlogistics.example";

/** A per-run suffix so suites can run repeatedly against the same demo database. */
export const RUN = process.env.E2E_RUN || Date.now().toString(36).slice(-5);

/** Fail the test on any console error the page reports. */
export function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

async function retrying(fn: () => Promise<import("@playwright/test").APIResponse>) {
  for (let i = 0; i < 10; i++) {
    const resp = await fn();
    if (resp.status() !== 429) return resp;
    await new Promise((r) => setTimeout(r, Number(resp.headers()["retry-after"] ?? "5") * 1000));
  }
  return fn();
}

/** An API client signed in as someone (waits out the sign-in limit if needed). */
export async function apiAs(email: string, password = PASSWORD): Promise<APIRequestContext> {
  const ctx = await request.newContext({ baseURL: BASE });
  const resp = await retrying(() => ctx.post("/api/v1/auth/login", { data: { email, password } }));
  expect(resp.status(), await resp.text()).toBe(200);
  return ctx;
}

/** JSON from the API, asserting success. */
export async function call<T = unknown>(ctx: APIRequestContext, method: string, path: string, data?: unknown): Promise<T> {
  const resp = await retrying(() => ctx.fetch(path, { method, data }));
  expect(resp.status(), `${method} ${path}: ${await resp.text()}`).toBeLessThan(300);
  return (resp.status() === 204 ? null : await resp.json()) as T;
}

/** A browser context carrying the session of an existing account. */
export async function signedInContext(browser: Browser, email: string): Promise<{ context: BrowserContext; page: Page }> {
  const api = await apiAs(email);
  const state = await api.storageState();
  await api.dispose();
  const context = await browser.newContext({ storageState: state });
  return { context, page: await context.newPage() };
}

export interface Project {
  id: string;
  org_id: string;
  name: string;
  slug: string;
}

export interface Connection {
  id: string;
  name: string;
  kind: string;
}

/** The seeded demo project by slug. */
export async function demoProject(api: APIRequestContext, slug = "operations"): Promise<Project> {
  const projects = await call<Project[]>(api, "GET", "/api/v1/projects");
  const found = projects.find((p) => p.slug === slug);
  expect(found, "run make demo first").toBeTruthy();
  return found as Project;
}

/** A seeded connection by name within the demo project. */
export async function demoConnection(api: APIRequestContext, projectId: string, name: string): Promise<Connection> {
  const conns = await call<Connection[]>(api, "GET", `/api/v1/projects/${projectId}/connections`);
  const found = conns.find((c) => c.name === name);
  expect(found, `connection "${name}" not found, run make demo first`).toBeTruthy();
  return found as Connection;
}
