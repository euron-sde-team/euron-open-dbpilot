/**
 * Query keys in one place, so invalidation after a mutation names exactly what it
 * touched. Everything inside a project starts with ["p", projectId], so leaving or
 * deleting a project drops all of it at once.
 */
export const qk = {
  me: ["auth", "me"] as const,
  meta: ["meta"] as const,

  orgs: ["orgs"] as const,
  org: (oid: string) => ["org", oid] as const,
  orgDashboard: (oid: string) => ["org", oid, "dashboard"] as const,

  projects: ["projects"] as const,
  project: (pid: string) => ["p", pid] as const,
  members: (pid: string) => ["p", pid, "members"] as const,
  overview: (pid: string) => ["p", pid, "overview"] as const,

  connections: (pid: string) => ["p", pid, "connections"] as const,
  connection: (pid: string, cid: string) => ["p", pid, "connection", cid] as const,
  connectionHealth: (pid: string, cid: string) => ["p", pid, "connection", cid, "health"] as const,
  grants: (pid: string, cid: string) => ["p", pid, "connection", cid, "grants"] as const,
  schema: (pid: string, cid: string) => ["p", pid, "schema", cid] as const,
  relationships: (pid: string, cid: string) => ["p", pid, "schema", cid, "rel"] as const,
  schemaSearch: (pid: string, cid: string, q: string) => ["p", pid, "schema", cid, "search", q] as const,
  snapshots: (pid: string, cid: string) => ["p", pid, "schema", cid, "snapshots"] as const,

  queries: (pid: string, filters: object = {}) => ["p", pid, "queries", filters] as const,
  query: (pid: string, qid: string) => ["p", pid, "query", qid] as const,
  queryRows: (pid: string, qid: string, offset: number, limit: number) => ["p", pid, "query", qid, "rows", offset, limit] as const,
  savedQueries: (pid: string, cid?: string) => ["p", pid, "saved-queries", cid ?? null] as const,

  generations: (pid: string) => ["p", pid, "generations"] as const,
  generation: (pid: string, gid: string) => ["p", pid, "generation", gid] as const,

  confirmations: (pid: string, status?: string) => ["p", pid, "confirmations", status ?? null] as const,
  confirmation: (pid: string, id: string) => ["p", pid, "confirmation", id] as const,

  backups: (pid: string, cid?: string) => ["p", pid, "backups", cid ?? null] as const,
  backup: (pid: string, id: string) => ["p", pid, "backup", id] as const,
  schedules: (pid: string) => ["p", pid, "schedules"] as const,

  comparisons: (pid: string) => ["p", pid, "comparisons"] as const,
  comparison: (pid: string, id: string) => ["p", pid, "comparison", id] as const,

  audit: (pid: string, filters: object = {}) => ["p", pid, "audit", filters] as const,
};

/** How long data stays fresh before a background refetch, by kind. */
export const STALE = {
  /** Something in motion (a confirmation about to execute). */
  live: 2_000,
  /** Lists that change when someone acts: refetched on focus and after mutations. */
  list: 15_000,
  /** One record being looked at. */
  detail: 10_000,
  /** Settings and catalogs that rarely change. */
  config: 60_000,
  /** A schema snapshot changes only on refresh. */
  static: 300_000,
} as const;
