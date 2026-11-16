/** Plain-words explanations for DBPilot vocabulary, shown as inline hints. */
export const GLOSSARY = {
  sqlGuard:
    "Before any AI-generated or human-typed statement runs, DBPilot parses it into a syntax tree and accepts it only if it is exactly one read-only query naming real tables and columns: no writes, no schema changes, no second statement.",
  readOnlySession:
    "The database session itself is opened read-only wherever the engine supports it (PostgreSQL and MySQL read-only transactions, SQLite query_only mode, SQL Server's own isolation check), so even a statement that slipped past the guard could not write.",
  aiNeverWrites:
    "DBPilot's AI assistant only ever generates a single read-only query. If the guard finds anything else, it is shown so you can see why, but it is refused for good: there is no override, no approval path, no 'run anyway'.",
  humanWrite:
    "A write you type and deliberately mark as intentional is never run on the spot. DBPilot describes exactly what will execute, asks you to confirm a second time, and records the result in the audit log before it reaches the database.",
  scanLimit:
    "Before a query runs, DBPilot asks the database's own planner how many rows it would read. Above this connection's scan limit, the query is refused instead of run.",
  rowLimit: "The most rows a result keeps. Rows past it are never fetched, and the result says it was cut.",
  maxRows: "The most rows a single query result returns to you.",
  timeout: "Seconds a statement may run before the database itself cancels it.",
  grant: "Who may use a connection. A project member with no grant cannot see the connection, its schema, or run anything against it: it is as if it does not exist for them.",
  canWrite: "Whether this grant lets the person prepare a confirmed write on the connection (they still confirm it separately before it runs).",
  digest: "A checksum of exactly what is about to run. Sending it back on confirm proves you are approving the statement you were shown, not a different one.",
  schemaSnapshot: "A point-in-time capture of a connection's tables, columns, indexes and foreign keys. The explorer, autocomplete, the AI assistant's context and schema comparison all read from the same snapshot.",
  comparison: "A diff between two schema snapshots, plus a generated migration script. DBPilot never runs that script itself: a human reviews it and runs it through the same confirmed-write path as any other write.",
  backup: "A full dump of a connection, taken through its connector, never a raw shell command against an untrusted path. A restore needs its own confirmation, just like a write.",
  weakConfig: "A real check against the connected engine's own setting (SSL requirement, read-only session support), not a guess.",
} as const;

export type GlossaryKey = keyof typeof GLOSSARY;
