# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

Initial release. See [docs/PLAN.md](docs/PLAN.md) for scope and acceptance criteria.

### Added

- **Connections**: PostgreSQL, MySQL, SQLite (uploaded file), SQL Server and MongoDB
  server connections, plus Redis. Saved connection profiles, connection testing, a
  per-connection safety envelope (`max_rows`, `max_scan_rows`, `timeout_s`), and a sealed
  secret that is never returned by any API response (AC1).
- **Database-enforced read-only, not just a parsed one**: PostgreSQL and MySQL via a real
  read-only session, SQLite via a private `mode=ro` copy with `PRAGMA query_only`, SQL
  Server via always-rolled-back nested transactions plus a nesting check, MongoDB and
  Redis via connector handles that expose read methods only (AC3).
- **Connection safety limits enforced before and after execution**: a planner-estimate
  check against `max_scan_rows` before a query runs, and a `truncated: true` flag on a
  result over `max_rows`, never a silent drop (AC2).
- **Database explorer**: tables/collections, columns, indexes, foreign keys, row-count
  estimates, an ER-style relationship view for the SQL engines, and row preview, all from
  a real schema discovery call against the live connection (AC6).
- **SQL editor**: syntax-aware SQL editing, autocomplete seeded from the real schema,
  query history, saved queries, CSV/JSON export, result pagination, for PostgreSQL, MySQL,
  SQLite and SQL Server.
- **Dedicated read-only panels for MongoDB and Redis**: a filter/aggregate builder and a
  key browser plus a read-only command allowlist, instead of a forced SQL abstraction
  (AC11).
- **AI database assistant**: natural-language-to-SQL generation against the real connected
  schema, SQL explanation, index suggestions, error diagnosis. Every generated statement
  goes through the SQL guard; anything but exactly one read-only query against identifiers
  the real schema actually returned is refused, unconditionally, with no override, no
  retry and no approval path anywhere (AC4, AC6).
- **A human-marked write requires a genuine two-step confirmation**: the statement is
  described and hashed into a digest, a one-time token is issued (only its hash is
  stored), and executing it requires sending both the exact token and the exact digest
  back. Three audit entries record the full trail: `write.requested`, `write.confirmed`,
  `write.executed` (AC5).
- **Backups**: manual and scheduled full backups for the SQL engines through the connector
  layer (never a raw shell-out), an integrity check that the dump round-trips, and a
  restore gated behind the identical two-step confirmation and audit trail as a write
  (AC7).
- **Schema comparison and migration scripts**: a diff between two schema snapshots (or a
  connection against a point in time) and a generated migration script that a human
  reviews and runs through the same confirmation path; DBPilot never executes a generated
  script itself (AC8).
- **Connection-level access control**: per-project roles (`owner`, `admin`, `viewer`) plus
  a connection grant model (an individual, or every project member, with or without the
  ability to mark writes). A connection with no grant answers 404 on every route, and so
  does everything derived from it: schema, history, AI generations, backups, comparisons,
  health (AC9).
- **An append-only audit log with one write path**: every query execution, AI generation
  (accepted or refused), confirmation step, backup, restore, migration run and
  connection-grant change writes through `audit/service.py`, the only code that inserts
  into `audit_log` (AC10).
- **A real weak-configuration check**: the connection health card reads the connected
  engine's own TLS/security setting live (Postgres's `ssl` GUC and `pg_stat_ssl`, MySQL's
  `require_secure_transport`, and the equivalent for SQL Server, MongoDB and Redis), never
  a hardcoded placeholder (AC13).
- **Live dashboard aggregates, not a worker-maintained illusion**: the per-connection
  health card and org-wide totals are computed from a live query or a live probe on every
  cache miss. The scheduled-backup worker (`dbpilot worker`) is a real, tested, registered
  CLI subcommand with a matching Compose service and Terraform ECS task definition, not a
  `command:` with nothing behind it (AC12).
- **Caching, documented and bounded**: a schema snapshot cache (1 hour TTL, invalidated
  explicitly or by a confirmed DDL write), a connection health response cache and a
  dashboard aggregate response cache (both 15 seconds), and a query result page cache tied
  to a query run's own immutability. Every layer degrades to a live read on a miss.
- **Dashboard (Next.js)**: sign in, Projects, project Overview, Connections (list, detail,
  new), SQL editor with the AI assist panel, MongoDB and Redis panels, write confirmation
  flow, Backups, Schema comparisons, Audit log, project Settings, instance System page.
  Dark and light themes, responsive to phone width.
- **Admin API**: everything the dashboard does, under `/api/v1`, OpenAPI documented
  (`docs/api/openapi.yaml`, exported from the live FastAPI app).
- **CLI** (`dbpilot`): `serve`, `worker`, `migrate`, `revision`, `create-admin`, `seed`,
  `openapi`, `mockai`.
- **Ops**: `/healthz`, `/readyz`, Prometheus `/metrics` (query, guard, AI generation,
  confirmation, backup, restore, connection test, schema discovery, cache, dependency
  error and audit counters and histograms), structured JSON logs, the append-only audit
  log, keyset-paginated (no 200-row cap).
- **Infrastructure as code**: Terraform modules for network, database, cache, storage,
  KMS, secrets, ECR, service and observability, with `local-floci` (applied automatically
  on `make up`, with `storage=s3` and `key_mode=kms` genuinely exercised against the
  emulator), `aws-dev` and `aws-prod` environments. Deliberately no queue/event module and
  no email module: the backend has no mail code path and its one background process polls
  Postgres directly (see [ADR-0004](docs/architecture/adr/0004-no-queue-or-mail-module.md)).

### Known limitations

- **In-database role/privilege auditing** is not implemented; 0.1.0 reports what DBPilot
  itself enforces, not a full audit of a connected database's own internal grants.
- **No approval workflow for AI-proposed writes.** This is by design (AC4): the AI never
  writes, full stop, rather than writing behind an approval screen.
- **Table-level and incremental backups** are not implemented; 0.1.0 backs up and restores
  a connection's full database.
- **Full infrastructure metrics** (CPU/memory/disk history beyond what a query against the
  database itself can see) are not implemented.
- **SDKs are not shipped in 0.1.0.**
