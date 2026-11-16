import { request } from "@playwright/test";

import { ADMIN, call, demoProject } from "./helpers";

/** Delete the saved queries this run created (their names end in the run suffix), so the
 * demo stack stays as `make demo` left it however often the suites run. */
export default async function globalTeardown(): Promise<void> {
  const baseURL = process.env.WEB_URL || "http://localhost:5810";
  const ctx = await request.newContext({ baseURL, storageState: ".auth/admin.json" });
  try {
    const resp = await ctx.post("/api/v1/auth/login", { data: ADMIN });
    if (!resp.ok()) return;
    const project = await demoProject(ctx);
    const saved = await call<{ id: string; name: string }[]>(ctx, "GET", `/api/v1/projects/${project.id}/saved-queries`);
    for (const q of saved) {
      if (q.name.includes(" e2e-")) await ctx.delete(`/api/v1/projects/${project.id}/saved-queries/${q.id}`);
    }
  } finally {
    await ctx.dispose();
  }
}
