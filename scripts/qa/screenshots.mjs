/**
 * Curated README screenshots and tour frames from a freshly seeded stack.
 *
 *   make up && make demo
 *   cd scripts/qa && QA_BASE=http://localhost:5810 node screenshots.mjs
 *   ./make-tour.sh        (stitches the frames into docs/assets/tour.gif)
 *
 * Writes docs/assets/screenshots/NN-name.png and scripts/qa/output/tour/NN.png.
 *
 * DBPilot's dashboard keeps an httpOnly session cookie (apps/web/src/lib/api/client.ts),
 * so this signs in through the real login form in a real page navigation, the same way
 * qa.mjs does, rather than seeding localStorage with a token.
 */
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.QA_BASE ?? "http://localhost:5810";
const DEMO_EMAIL = process.env.QA_EMAIL ?? "maya.chen@lumenlogistics.example";
const PASSWORD = process.env.DBPILOT_DEMO_PASSWORD ?? process.env.QA_PASSWORD ?? "";
const CHROME = process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH) ? process.env.CHROME_PATH : undefined;
const SHOTS = path.resolve(HERE, "../../docs/assets/screenshots");
const TOUR = path.resolve(HERE, "output/tour");
const DESKTOP = { width: 1440, height: 900, deviceScaleFactor: 1 };
const PHONE = { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 2 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let tourIndex = 0;

async function settle(page, ms = 900) {
  await page.waitForNetworkIdle({ idleTime: 400, timeout: 20_000 }).catch(() => {});
  await sleep(ms);
}

async function shot(page, name, { tour = true, fullPage = true } = {}) {
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage });
  if (tour) await page.screenshot({ path: path.join(TOUR, `${String(++tourIndex).padStart(2, "0")}.png`) });
  console.log(`saved ${name}`);
}

async function api(url) {
  const resp = await fetch(`${BASE}${url}`, { headers: { cookie: global.__cookie } });
  if (!resp.ok) throw new Error(`GET ${url}: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

async function signInForm(page, email, theme) {
  await page.evaluateOnNewDocument((t) => {
    try {
      localStorage.setItem("theme", t);
    } catch {}
  }, theme);
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle2" });
  await page.type("#email", email);
  await page.type("#password", PASSWORD);
  await Promise.all([page.waitForNavigation({ waitUntil: "networkidle2" }), page.click("button[type=submit]")]);
}

async function goto(page, urlPath) {
  await page.goto(`${BASE}${urlPath}`, { waitUntil: "networkidle0", timeout: 30_000 });
  await settle(page);
}

async function main() {
  await mkdir(SHOTS, { recursive: true });
  await mkdir(TOUR, { recursive: true });
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"] });

  // Resolve ids with a plain fetch login first (server-side, no browser needed).
  const loginResp = await fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: DEMO_EMAIL, password: PASSWORD }),
  });
  if (!loginResp.ok) throw new Error(`sign-in failed (${loginResp.status}): run make demo first`);
  global.__cookie = loginResp.headers.get("set-cookie");
  const projects = await api("/api/v1/projects");
  const op = projects.find((p) => p.slug === "operations");
  if (!op) throw new Error("the demo project is missing: run make demo");
  const conns = await api(`/api/v1/projects/${op.id}/connections`);
  const shop = conns.find((c) => c.name === "Shop (PostgreSQL)")?.id;

  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  await page.setViewport(DESKTOP);
  await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  await signInForm(page, DEMO_EMAIL, "light");

  // 1. Projects: the seeded organisation, alive from the first sign-in.
  await goto(page, "/projects");
  await shot(page, "01-projects");

  // 2. Project overview: health cards and org-wide totals.
  await goto(page, `/projects/${op.id}`);
  await shot(page, "02-overview");

  // 3. Connections: every engine, one card per connection, no secret in sight.
  await goto(page, `/projects/${op.id}/connections`);
  await shot(page, "03-connections");

  // 4. A connection's schema explorer (real discovery, AC6).
  await goto(page, `/projects/${op.id}/connections/${shop}`);
  await shot(page, "04-connection-detail");

  // 5. SQL editor with the AI assist panel open.
  await goto(page, `/projects/${op.id}/editor?connection=${shop}&panel=ai`);
  await shot(page, "05-sql-editor-ai");

  // 6. Backups.
  await goto(page, `/projects/${op.id}/backups`);
  await shot(page, "06-backups");

  // 7. Schema comparisons.
  await goto(page, `/projects/${op.id}/comparisons`);
  await shot(page, "07-comparisons");

  // 8. Audit log: real seeded history. Not full-page: the log is long and a single
  // viewport-height capture is the more useful screenshot.
  await goto(page, `/projects/${op.id}/audit`);
  await shot(page, "08-audit-log", { fullPage: false });

  await page.close();

  // 9. Dark mode: Overview again, for the one mandatory dark capture. A fresh page so
  // next-themes reads "theme=dark" from localStorage before it ever mounts.
  const darkPage = await ctx.newPage();
  await darkPage.setViewport(DESKTOP);
  await darkPage.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "dark" }]);
  await signInForm(darkPage, DEMO_EMAIL, "dark");
  await goto(darkPage, `/projects/${op.id}`);
  await shot(darkPage, "09-overview-dark");
  await darkPage.close();

  // 10. Phone width: Overview, light mode.
  const phonePage = await ctx.newPage();
  await phonePage.setViewport(PHONE);
  await phonePage.emulateMediaFeatures([{ name: "prefers-color-scheme", value: "light" }]);
  await signInForm(phonePage, DEMO_EMAIL, "light");
  await goto(phonePage, `/projects/${op.id}`);
  await shot(phonePage, "10-overview-phone", { fullPage: false });
  await phonePage.close();

  await browser.close();
  console.log(`\nwrote ${tourIndex} tour frames to ${TOUR}`);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
