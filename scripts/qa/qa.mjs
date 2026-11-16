/**
 * Browser QA (make qa): signs in through the real form as each role, visits every page in
 * light and dark at desktop and phone width, saves screenshots, and fails on console
 * errors, failed or 4xx/5xx requests, broken images, error states, empty pages with no
 * explanation, and horizontal overflow.
 *
 *   make qa
 *   QA_BASE=http://localhost:5810 QA_OUT=./output node qa.mjs
 *   QA_PAGES=/projects QA_ROLES=owner QA_THEMES=dark QA_SIZES=desktop node qa.mjs
 *
 * Needs the seeded stack (make up && make demo). Uses Puppeteer's bundled Chromium; set
 * CHROME_PATH to use a local browser instead.
 *
 * DBPilot's dashboard keeps its session in an httpOnly cookie (see
 * apps/web/src/lib/api/client.ts), not a token in localStorage, so every role here signs
 * in through the real login form rather than seeding storage.
 */
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import puppeteer from "puppeteer";

const BASE = process.env.QA_BASE ?? "http://localhost:5810";
const OUT = process.env.QA_OUT ?? path.resolve("output");
const PASSWORD = process.env.QA_PASSWORD ?? process.env.DBPILOT_DEMO_PASSWORD ?? "";
const CHROME = process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH) ? process.env.CHROME_PATH : undefined;

const OWNER = "maya.chen@lumenlogistics.example";
const ADMIN = "arjun.rao@lumenlogistics.example";
const VIEWER = "sofia.alvarez@lumenlogistics.example";
// /admin/system needs the INSTANCE admin role (auth/dependencies.py's InstanceAdmin), a
// different thing from a project owner/admin: the demo seed creates only project roles,
// so this account is created here, idempotently, through the real `dbpilot create-admin`
// CLI (never by hand-editing the database), the same bootstrap step a real operator runs.
const INSTANCE_ADMIN = process.env.QA_INSTANCE_ADMIN_EMAIL ?? "qa-instance-admin@lumenlogistics.example";

// Pages per role. Placeholders are resolved from the seeded data once.
const P = "/projects/:op";
const ROLE_PAGES = {
  anonymous: ["/login"],
  owner: [
    "/projects",
    P,
    `${P}/connections`,
    `${P}/connections/:shop`,
    `${P}/connections/:sqlite`,
    `${P}/connections/:mongo`,
    `${P}/connections/:redis`,
    `${P}/editor?connection=:shop`,
    `${P}/editor?connection=:shop&panel=ai`,
    `${P}/editor?connection=:mongo`,
    `${P}/editor?connection=:redis`,
    `${P}/backups`,
    `${P}/comparisons`,
    `${P}/audit`,
    `${P}/settings`,
  ],
  admin: [P, `${P}/connections`, `${P}/editor?connection=:shop`, `${P}/audit`],
  viewer: [P, `${P}/connections`, `${P}/editor?connection=:shop`],
  instanceAdmin: ["/admin/system"],
};
const ONLY = process.env.QA_PAGES ? new Set(process.env.QA_PAGES.split(",")) : null;
const ROLES = (process.env.QA_ROLES ?? "anonymous,owner,admin,viewer,instanceAdmin").split(",");
const THEMES = (process.env.QA_THEMES ?? "light,dark").split(",");
const SIZES = {
  desktop: { width: 1440, height: 900 },
  phone: { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
};
const SIZE_KEYS = (process.env.QA_SIZES ?? "desktop,phone").split(",");
const FULL = process.env.QA_FULL !== "0";

function slug(p) {
  return p.replace(/^\//, "").replace(/[/:?=&]/g, "-") || "home";
}

/** Sign in through the real form, so the httpOnly session cookie is set the same way a
 * person's browser would set it. */
async function signInForm(page, email) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle2" });
  await page.type("#email", email);
  await page.type("#password", PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2" }), page.click("button[type=submit]")]);
}

async function api(cookie, method, url, body) {
  const resp = await fetch(`${BASE}${url}`, {
    method,
    headers: { cookie, "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) throw new Error(`${method} ${url}: ${resp.status} ${await resp.text()}`);
  return resp.status === 204 ? null : resp.json();
}

async function loginCookie(email) {
  const resp = await fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password: PASSWORD }),
  });
  if (!resp.ok) throw new Error(`sign-in for ${email} failed (${resp.status}): run make demo first`);
  return resp.headers.get("set-cookie");
}

/** Ids of seeded records for the parameterised pages. */
async function resolveIds() {
  const cookie = await loginCookie(OWNER);
  const projects = await api(cookie, "GET", "/api/v1/projects");
  const op = projects.find((p) => p.slug === "operations");
  if (!op) throw new Error("the demo project is missing: run make demo");
  const conns = await api(cookie, "GET", `/api/v1/projects/${op.id}/connections`);
  const byName = (name) => conns.find((c) => c.name === name)?.id ?? "missing";
  return {
    op: op.id,
    shop: byName("Shop (PostgreSQL)"),
    sqlite: byName("Subscriptions (SQLite)"),
    mongo: byName("Catalog (MongoDB)"),
    redis: byName("Sessions (Redis)"),
  };
}

function watch(page, sink) {
  page.on("console", (m) => {
    if (m.type() === "error") sink.push(m.text());
  });
  page.on("pageerror", (e) => sink.push(`pageerror: ${e.message}`));
  page.on("requestfailed", (r) => {
    const err = r.failure()?.errorText ?? "";
    if (!err.includes("ERR_ABORTED")) sink.push(`requestfailed ${r.url()} ${err}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 400) sink.push(`HTTP ${r.status()} ${r.url()}`);
  });
}

async function inspect(page) {
  return page.evaluate(() => {
    const overflow = document.documentElement.scrollWidth - window.innerWidth;
    const broken = [...document.images].filter((img) => img.complete && img.naturalWidth === 0).map((img) => img.src);
    const errorStates = [...document.querySelectorAll("[data-qa='error-state']")].map((el) => el.textContent?.slice(0, 80) ?? "");
    const main = document.querySelector("main") ?? document.body;
    const text = (main.textContent ?? "").replace(/\s+/g, " ").trim();
    return { overflow, broken, errorStates, empty: text.length < 40 };
  });
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const ids = await resolveIds();
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-sandbox"] });
  const problems = [];
  let visited = 0;

  for (const role of ROLES) {
    const pages = (ROLE_PAGES[role] ?? []).filter((p) => !ONLY || ONLY.has(p));
    if (!pages.length) continue;
    for (const theme of THEMES) {
      for (const sizeKey of SIZE_KEYS) {
        const ctx = await browser.createBrowserContext();
        const page = await ctx.newPage();
        await page.setViewport(SIZES[sizeKey]);
        await page.evaluateOnNewDocument((t) => {
          try {
            localStorage.setItem("theme", t);
          } catch {}
        }, theme);
        const errors = [];
        watch(page, errors);
        const who = { owner: OWNER, admin: ADMIN, viewer: VIEWER, instanceAdmin: INSTANCE_ADMIN }[role];
        if (who) await signInForm(page, who);
        for (const p of pages) {
          const url = p.replace(/:(\w+)/g, (_, k) => ids[k] ?? k);
          errors.length = 0;
          await page.goto(`${BASE}${url}`, { waitUntil: "networkidle2", timeout: 60_000 });
          await new Promise((r) => setTimeout(r, 1200));
          const found = await inspect(page);
          const file = path.join(OUT, `${role}-${sizeKey}-${theme}-${slug(p)}.png`);
          await page.screenshot({ path: file, fullPage: FULL });
          const issues = [...errors];
          if (found.overflow > 1) issues.push(`horizontal overflow ${found.overflow}px`);
          for (const src of found.broken) issues.push(`broken image ${src}`);
          for (const text of found.errorStates) issues.push(`error state: ${text}`);
          if (found.empty) issues.push("page is empty with no explanation");
          if (issues.length) problems.push({ role, page: p, theme, size: sizeKey, issues });
          visited += 1;
          console.log(`${issues.length ? "WARN" : "ok  "} ${role} ${sizeKey} ${theme} ${p}${issues.length ? `: ${issues.join(" | ")}` : ""}`);
        }
        await ctx.close();
      }
    }
  }
  await browser.close();
  console.log(`\n${visited} page views, ${problems.length} with problems. Screenshots in ${OUT}`);
  if (problems.length) process.exitCode = 1;
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
