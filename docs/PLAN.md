# Euron Open DBPilot: plan for 0.1.0

Source brief: the user's pasted "Euron DB Pilot: Complete Product Blueprint" (received
2026-11-13). The brief's 100 features across 10 areas and 7-phase roadmap are the source
of truth for what DBPilot should eventually do; 0.1.0 scopes Phase 1 (core foundation),
Phase 2 (database explorer), Phase 3 (SQL workspace) and the parts of Phase 4 to 6 that
are genuine safety or usability requirements rather than growth features (see Scope
below). Where the brief and the program rules differ, the rules win: MIT licence (brief:
Apache 2.0), the standard repo layout with domain packages under `apps/api` and Next.js
(brief: `frontend/`, `backend/`, plain React), the program's own Postgres-backed worker
(brief: Celery/Redis).

## Purpose

DBPilot is a self-hosted, team-shared database operations dashboard. A team registers a
database connection once (PostgreSQL, MySQL, SQLite, SQL Server, MongoDB or Redis), and
every teammate who signs in to the dashboard can browse its schema, run queries, and
watch its health, without ever holding that database's own credential: the credential is
sealed server side, and a person authenticates against DBPilot, never against the raw
database.

The outcome it guarantees: **an AI-generated query, or a query a human types but did not
explicitly mark as a write, only ever runs as a read-only statement against a connection
the database itself has restricted to read-only, and anything that is NOT read-only
(an `INSERT`/`UPDATE`/`DELETE`/DDL statement, a migration, a restore) requires an
explicit, separately-confirmed action and is fully recorded in the audit log: who ran it,
what it was, and when.** Every finding from today's market research (see below) is that
this exact guarantee is the one thing no comparable open-source or commercial tool
provides as a first-class, un-paywalled feature.

The name is the contract. A *pilot* for a database must (1) let a team fly it together
from one shared cockpit, not hand out individual keys to the engine room, (2) show
exactly what is about to happen before a risky action runs, never execute a write or a
restore silently, and (3) keep instruments (schema, health, backups, audit) visible at
all times, not buried behind a paid tier.

## Comparable products and what users expect

| Product | License | Self-hostable | Notes |
| --- | --- | --- | --- |
| Beekeeper Studio | OSS (MIT core) | Desktop app only, per-developer | No team-shared connections; AI is bring-your-own-key; weak on large datasets, no ER diagrams |
| DBeaver | OSS Community + paid PRO | Desktop app only, per-developer | Widest engine coverage; AI assistant and most governance features gated behind the paid PRO tier; single-user by design |
| pgAdmin | OSS | Runs as a local web app, still single-user | Postgres only; long-standing complaints about a slow, clunky "desktop app in a browser shell" |
| Metabase | OSS core + paid Pro | Yes, true multi-tenant web app | Closest existing product to "team dashboard over data," but it is a BI/query tool: no schema editing, no migrations, no backups, no server monitoring; AI usage governance (caps, per-group restrictions) is Pro-only |
| TablePlus | Commercial, desktop | No | The UX benchmark for a SQL editor, but no team concept, no web access, no backup/migration tooling |
| Outerbase / QoreDB | Hosted or open-core desktop | Partial | Show the AI-generated query before running it (a good transparency pattern DBPilot adopts), but neither enforces a database-level read-only boundary or an approval queue for writes |

Expected by users of this category (table stakes): connect the common relational
databases with saved connections and schema discovery; a SQL editor with syntax
highlighting, history and CSV export; basic health/performance monitoring; manual
backups. None of the above make a shared team connection, an enforced read-only
boundary, or backup/migration visibility a first-class free feature; Metabase comes
closest on the team axis but has no admin/ops surface at all.

**Why ours is worth starring:**

1. **Read-only is a database-enforced boundary, not a UI convention.** Every comparable
   tool either leaves write access open by default or, at best (Outerbase), shows the
   query before running it with no enforcement behind that display. DBPilot parses every
   query that is not explicitly flagged as an intentional write through a SQL guard (AST
   level, reusing the design proven in Euron Open SQLAgent's `sql/guard.py` and Euron
   Open DataQuality's `sources/sql.py`) and opens the underlying connection itself
   read-only wherever the engine supports it, so a query that slipped past the parser
   still cannot write.
2. **Credentials never leave the server.** Every desktop tool (DBeaver, TablePlus,
   Beekeeper) stores the raw database password on each developer's machine and shares it
   out of band (Slack, a password manager). DBPilot seals every connection's secret
   server side (envelope encryption, the same pattern as every other Euron Open vault)
   and a teammate never sees or needs the underlying password to use a connection they
   have access to.
3. **No open-core bait-and-switch.** DBeaver's AI assistant and Metabase's AI governance
   are both paid-tier features. DBPilot is MIT, fully usable from `docker compose up`,
   with nothing held back behind a paid tier, the same promise every other Euron Open
   project makes.
4. **A real multi-engine connector layer, not just Postgres.** PostgreSQL and MySQL are
   first class in 0.1.0 (per the brief's own MVP checklist); SQLite and SQL Server follow
   the same read-only-connection pattern DataQuality already proved works; MongoDB and
   Redis get their own read-only explorer and query surfaces rather than being forced
   through a SQL abstraction that does not fit them.

## Scope for 0.1.0

In scope (brief sections A, B, C, E, F, G, H, and the AI slice of D needed to make the
read-only guarantee real; brief section I/J mostly Roadmap):

- **Connections** (brief A): PostgreSQL, MySQL, SQLite (uploaded file), SQL Server,
  MongoDB, Redis. Saved connection profiles, connection testing, safety limits per
  connection (max rows, max scan rows, statement timeout), sealed secrets.
- **Database explorer** (brief B): browse schemas/databases, tables/collections, columns,
  indexes, foreign keys; an ER-style relationship view for the SQL engines; row preview;
  search across database objects.
- **SQL / query workspace** (brief C): a SQL editor (syntax highlighting, autocomplete
  seeded from the real schema, formatting, history, saved queries) for the SQL engines; a
  parallel read-only query surface for MongoDB (filter/aggregate) and Redis (key browser,
  read-only commands); CSV/JSON export; result pagination.
- **AI database assistant, the safety-critical slice** (brief D, items 31 to 34, 37):
  natural-language-to-SQL generation against the real connected schema, SQL explanation,
  index suggestions from the real schema's missing-index patterns, error diagnosis. Every
  AI-generated query is tokenized exactly like SQLAgent's guard and shown before it runs;
  **0.1.0 never lets an AI-generated statement write**, full stop, not even with
  approval. The rest of brief D (schema design, dialect conversion, doc generation) is
  Roadmap.
- **Performance monitoring** (brief E): a health dashboard per connection (reachability,
  active connections, basic size/row counts), slow-query surfacing where the engine
  exposes it natively (`pg_stat_statements` when present, MySQL's slow query digest),
  index usage advisories. Full CPU/memory history graphing needs infrastructure metrics
  DBPilot cannot see from inside a customer's database and is Roadmap (documented
  honestly, not faked).
- **Backup and recovery** (brief F): manual and scheduled full backups for the SQL
  engines (`pg_dump`/`mysqldump`-equivalent through the connector layer, never a raw
  shell-out to an untrusted path), backup listing, **restore gated behind an explicit
  confirmation step and its own audit entry**, integrity check (the dump round-trips).
  Table-level and incremental backups are Roadmap.
- **Schema and migration management** (brief G): schema comparison between two
  connections (or two points in time on the same connection) for the SQL engines, a
  generated migration script (DDL diff) a human reviews and explicitly runs; DBPilot
  itself never auto-applies a generated migration. Migration rollback planning and
  version tracking beyond "the last N comparisons" are Roadmap.
- **Security and access management, the parts that are genuinely 0.1.0-sized** (brief H):
  per-project roles (owner/admin/viewer) plus a connection-level grant model (who can see
  which connection, and whether they may mark a query as an intentional write); an
  append-only audit log of every query run, every write/restore/migration executed and
  by whom; a weak-configuration check (an engine's own `*_READ_ONLY`/`require_ssl` style
  settings) surfaced on the connection's health card. Role/privilege auditing **inside**
  the connected database (brief 72/73/77) is Roadmap: 0.1.0 reports what DBPilot itself
  enforces, not a full audit of the target database's own grants.

Out of scope for 0.1.0, explicit Roadmap (brief I, J, and the rest of D/E/F/G above):
webhooks, a public REST API beyond the admin API every project ships, CI/CD integration,
Git-based migration workflows, multi-environment promotion, cost/resource reporting,
documentation portals, table-level/incremental backups, full infra-metrics monitoring,
in-database role/privilege auditing.

## Data model (apps/api/src/dbpilot/, domain packages)

- `orgs`: organisations, users, roles (owner/admin/viewer).
- `projects`: one workspace per team; every connection, query and audit entry is scoped
  to a project.
- `connections`: name, kind (`postgresql`/`mysql`/`sqlite`/`mssql`/`mongodb`/`redis`),
  non-secret config (host/port/database/username/ssl mode), `secret_sealed` (envelope
  encrypted), per-connection safety limits (`max_rows`, `max_scan_rows`, `timeout_s`),
  last-tested status, server version. A per-connection grant table lists which users (or
  all project members) may use it, and whether they may mark a query as an intentional
  write.
- `schema_snapshots`: a point-in-time capture of a connection's discovered schema
  (tables, columns, indexes, foreign keys, row-count estimates), used for autocomplete,
  the AI assistant's context, the ER view and schema comparison.
- `queries`: every query run (read-only or an explicitly-marked write), its SQL/filter
  text, the connection, who ran it, duration, row count, whether the AI generated it.
- `ai_generations`: a natural-language prompt, the schema context sent, the generated
  SQL, and whether it passed the guard (every generation is retained even when refused,
  for the audit trail).
- `backups`: connection, started/finished, size, storage location, integrity-check
  result, triggered-by (manual or scheduled).
- `schema_comparisons`: two schema snapshots (or a connection against a point in time),
  the generated diff, the migration script a human may choose to run.
- `audit_log`: append-only; every query execution, write confirmation, restore, migration
  run, connection grant change.

## Caching layers (documented per the program's own quality bar)

- **Schema snapshot cache**: a connection's latest discovered schema, Redis (or
  in-process memory without Redis) with a bounded TTL; invalidated explicitly when a user
  asks to re-discover, or automatically after a configured interval. The autocomplete and
  AI-context paths read the cache; a cache miss re-discovers from the live connection.
- **Connection health cache**: the dashboard's per-connection health card, a short TTL
  response cache (15s, same pattern as every sibling project's dashboard summary),
  computed live from a lightweight reachability/size query, never a separately
  maintained rollup table.
- **Dashboard aggregate cache**: org-wide totals (connections, queries run, backups,
  pending restores), same 15s response-cache pattern.

## Ports (base 5800; project 18 of the program)

web 5810 / api 5830 / pg 5842 / redis 5879 / floci 5866 (mailpit 5825, mock AI 5850,
tf-aws-on-floci 5896). Renumbered from the originally planned project 16 / base 5600
after discovering, during live verification, that project 16 was a duplicate number
also assigned to TestForge, giving both projects an identical colliding port block;
18 was the next free project number in PROJECTS.md. Demo source databases for the
connector layer run as their own Compose services, outside this port block, matching
DataQuality's own pattern: demo Postgres source 5843, demo MySQL 5806, demo SQL
Server 5807, demo MongoDB 5808, demo Redis source 5809.

## Acceptance criteria (0.1.0)

1. **AC1 (connections, sealed secrets)**: creating a connection never returns the secret
   in any API response, ever; the stored `secret_sealed` can only be opened server side
   at the moment a connection is actually used.
2. **AC2 (connection safety limits enforced)**: a query against a connection that would
   scan more rows than that connection's `max_scan_rows` is refused before it runs (an
   `EXPLAIN`-based estimate, never "run first, cut later" for the scan limit); a result
   larger than `max_rows` is truncated with `truncated: true` in the response, never
   silently dropped without saying so.
3. **AC3 (read-only is database-enforced, not just parsed)**: a query that bypasses the
   SQL guard (a crafted statement the parser accepts but is not actually read-only)
   still cannot write, because the connection itself is opened with the engine's own
   read-only session setting wherever the engine supports one (Postgres
   `default_transaction_read_only`, MySQL `SET SESSION TRANSACTION READ ONLY`, SQLite
   `mode=ro`+`PRAGMA query_only`, SQL Server `SET TRANSACTION ISOLATION LEVEL` plus an
   application-enforced check, MongoDB/Redis through an explicit read-only command
   allowlist since neither engine has a native read-only session flag).
4. **AC4 (AI-generated SQL never writes, full stop)**: an AI-generated statement that the
   guard identifies as anything other than a single read-only query is refused before
   execution, with no override, no approval path, no "run anyway" button anywhere in
   0.1.0, for an AI-generated statement specifically (see AC5 for human-marked writes).
5. **AC5 (a human-marked write requires explicit confirmation and is fully audited)**: a
   human who deliberately marks a query as a write (never the AI assistant) gets a second,
   separate confirmation step describing exactly what will run, and the execution is
   recorded in the audit log with the actor, the exact statement, the connection, and the
   result, before the statement reaches the database.
6. **AC6 (schema discovery is real, not a guess)**: the database explorer and the AI
   assistant's context both come from the same real schema-discovery call against the
   live connection (tables, columns, types, primary/foreign keys, indexes, row-count
   estimates); the AI assistant never fabricates a column or table name that does not
   exist in the discovered schema's own result.
7. **AC7 (backups are real and restorable)**: a backup created by DBPilot can be restored
   by DBPilot into a fresh database and the resulting schema and row counts match the
   source at backup time; a restore requires its own explicit confirmation and audit
   entry, exactly like AC5's write confirmation.
8. **AC8 (schema comparison produces a runnable, human-reviewed migration)**: comparing
   two schema snapshots produces a diff and a generated migration script; DBPilot never
   executes that script itself, only a human running it through the same write-confirmation
   path in AC5.
9. **AC9 (connection-level access control)**: a project member with no grant on a
   connection cannot see that connection, its schema, or run any query against it through
   any API route; this is a 404, not a 403, matching the program's structural-isolation
   convention.
10. **AC10 (the audit log cannot be bypassed)**: every query execution, AI generation
    (accepted or refused), write confirmation, backup, restore and migration run writes
    an audit entry through one shared code path; there is no second way to reach the
    database's connector layer that skips it.
11. **AC11 (multi-engine parity where it matters)**: PostgreSQL and MySQL support every
    0.1.0 feature (explorer, SQL editor, AI assistant, monitoring, backup, schema
    compare); SQLite and SQL Server support explorer, SQL editor, AI assistant and
    backup/restore; MongoDB and Redis support their own read-only explorer and query
    surface, documented honestly as not supporting SQL-editor-specific features (schema
    compare, AI-to-SQL) that do not apply to them.
12. **AC12 (dashboard aggregates are live, not a worker-maintained illusion)**: the
    per-connection health card and the org-wide dashboard totals are computed from a live
    query against DBPilot's own Postgres on a cache miss; if a background worker exists
    for scheduled backups, it is a real, tested, registered CLI subcommand
    (`dbpilot worker`), never a `command:` in Compose with no matching implementation
    (the exact mistake the program's own PIIShield release made and documented; see
    `euron-open-source-program` memory before writing a single line of worker-related
    compose/Terraform).
13. **AC13 (a weak-configuration check is real, not cosmetic)**: the connection health
    card's security check reads the actual connected engine's own setting (e.g.
    Postgres's `ssl` GUC, MySQL's `require_secure_transport`) through a real query against
    that engine, never a hardcoded "looks fine" placeholder.
14. **AC14 (SDKs, if shipped in 0.1.0, are wire-compatible with the real API)**: if
    Python/Node SDKs ship in 0.1.0, both are asserted byte-identical against the real
    live API via a shared fixture generated from a live scan, the same discipline as
    every sibling project's SDK pair, not hand-guessed values (see the PIIShield lesson
    in `euron-open-source-program` memory about per-recognizer vs. coarse-category
    naming: get every enum/category value from the real running code, never a guess).

## Roadmap (explicit, not silently dropped)

Webhooks, public automation API beyond the admin API, CI/CD and Git-based migration
workflows, multi-environment promotion, cost/resource reporting, a documentation portal,
table-level and incremental backups, full infrastructure metrics (CPU/memory/disk history
beyond what a query against the database itself can see), in-database role/privilege
auditing, MariaDB/Oracle/CockroachDB/Aurora connectors, AI-assisted schema design and
dialect conversion, approval workflows for AI-proposed writes (0.1.0's answer is simpler
and safer: AI never writes at all).
