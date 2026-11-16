# Low-level design

How [HLD.md](HLD.md)'s processes are actually built: the package layout, the one door to a
customer database, the guard, confirmations, and where each acceptance criterion lives in
the code.

## Package layout

`apps/api/src/dbpilot/`, one package per bounded domain, 131 Python source files:

```
core/            config.py (Settings), db.py, kv.py, errors.py, security.py, sealing.py,
                 storage.py, metrics.py, logs.py, middleware.py, net.py, aws.py, tracing.py
orgs/            organizations, users, membership
projects/        projects, project members, roles (owner/admin/viewer)
auth/            sessions, login, revoked sessions
connections/     connections, connection grants, access.py (AC9), gateway.py (the one door),
                 connectors/ (engine-dispatch: sql.py, mongo.py, redis_source.py, openers.py,
                 discovery.py, health.py, health_sql.py, dump.py, targets.py, values.py)
schema/          schema snapshot discovery, cache, read model for the explorer and AI
queries/         guard.py (the SQL guard), the query-run path, saved queries, writes.py
ai/              natural-language-to-SQL generation, explanation, advice, identifiers.py
confirmations/   the two-step confirm (write, migration, restore), token and digest
backups/         manual and scheduled backups, schedules, restore
comparisons/     schema comparison between two snapshots, migration script generation
monitoring/      the per-connection health card, weak-configuration checks
dashboard/       org-wide aggregates (cached)
audit/           the one write path for the audit log
demo/            the `dbpilot seed` demo data and demo source databases
mockai/          the deterministic mock model provider (tests, local dev, demo)
migrations/      Alembic environment and versions
system/          /healthz, /readyz, worker heartbeats, instance info
main.py          create_app(): wires routers, middleware, lifespan
worker.py        the scheduled-backup worker loop
cli.py           the `dbpilot` entry point: serve, worker, migrate, revision, create-admin,
                 seed, openapi, mockai
```

Each domain follows `router.py` (HTTP only) -> `service.py` (business rules) ->
`repository.py` or inline queries -> `models.py`. A domain calls another domain's service,
never its models directly.

## The one door to a customer database (AC1, AC3, AC9, AC10)

`connections/gateway.py` is the only module that opens a connection to a database a user
registered; `tests/unit/connections/test_one_door.py` asserts by import graph that nothing
else in the package imports `connectors/openers.py` directly. Every call through the
gateway:

1. resolves access through `connections/access.py` (`accessible()`), which 404s instead of
   403ing when the caller has no grant (AC9);
2. opens the connection with the engine's own read-only enforcement where one exists
   (`connectors/openers.py`, detailed in [security.md](security.md), AC3);
3. runs exactly one statement through the extended query protocol (never the simple
   protocol, which a crafted multi-statement string could otherwise escape through);
4. writes one `query_runs` row and one audit entry before returning, whether the statement
   succeeded, was refused, or errored (AC10).

## The SQL guard (AC4)

`queries/guard.py` parses every statement with `sqlglot` in the connection's own dialect
and accepts only a single read-only query. It is called from two places with different
consequences:

- **Human-typed, not marked as a write**: a refusal is shown in the editor; the person can
  mark the statement as an intentional write, which routes it to confirmations instead
  (below), never around the guard.
- **AI-generated** (`ai/service.generate_sql`): a refusal here is final. There is no retry,
  no approval, no "run anyway." The generation is saved with `status="refused"` and its
  guard code/reason, so the refusal itself is part of the audit trail (AC4). Running an
  *accepted* generation (`ai/service.run_generated`) sends it through the exact same guard
  a second time before it reaches the gateway, so a generation cannot be mutated between
  acceptance and execution to smuggle in a write.

`ai/identifiers.py` additionally refuses a generated statement that names a table or column
the real schema discovery never returned, so the assistant cannot fabricate a target that
happens to parse as read-only (AC6).

## Confirmations: the second step (AC5, AC7, AC8)

`confirmations/service.py` backs every human-marked write, every schema-comparison
migration a human chooses to run, and every backup restore, through the identical
two-step flow:

1. **`create`**: the exact statement or restore plan is hashed into a `digest`; a one-time
   token is generated and only its hash is stored (`confirmations.models.Confirmation`);
   an audit entry (`write.requested` / `migration.requested` / `restore.requested`) is
   written. Status `pending`, with an expiry.
2. **`begin`**: the caller sends the token and the digest back. Both must match exactly what
   was stored (`constant_time_equals`), the confirmation must still be `pending` and not
   expired, or the attempt is rejected and the reason is recorded. This is the point that
   makes the confirmation genuinely a second, independent step rather than a checkbox: a
   caller who only has the confirmation id (no token, no digest) cannot execute it.
3. **`finish`**: the statement actually runs through the same gateway every query uses,
   and a `write.confirmed` / `write.executed` pair of audit entries (or `migration.*`,
   `restore.*`) is written around it, with the actor, the exact statement, the connection
   and the result.

Stale pending confirmations are expired by the worker (`confirmations.service.expire_stale`,
called from `worker.py`'s housekeeping pass), never silently left runnable forever.

## Dashboard and health aggregates (AC12, AC13)

`dashboard/service.py` and `monitoring/service.py` both compute their numbers from a live
query against DBPilot's own Postgres (or, for health, a live probe of the connected
engine) on every cache miss; nothing is precomputed by the worker. The worker
(`worker.py`) exists only for scheduled backups, confirmation expiry and its own heartbeat,
registered as the real `dbpilot worker` CLI subcommand and a genuine Compose/Terraform
service, not a `command:` with no matching implementation.

The weak-configuration check (`connections/connectors/health.py`'s `tls_check`,
`health_sql.py`'s per-engine SSL/TLS probes) reads the connected engine's own setting
(Postgres `SHOW ssl` and `pg_stat_ssl`, MySQL/MSSQL/Mongo/Redis their own TLS state) through
a live query every time the health card refreshes; it is never a hardcoded "looks fine".

## Where each acceptance criterion lives

| AC | Enforced in |
| --- | --- |
| AC1 sealed secrets | `connections/models.py` (`secret_sealed`), `core/sealing.py`, `connections/schemas.py` (no secret field on any response model) |
| AC2 safety limits | `connections/connectors/sql.py` (planner estimate vs `max_scan_rows`), `queries/service.py` (`truncated` flag vs `max_rows`) |
| AC3 database-enforced read-only | `connections/connectors/openers.py` |
| AC4 AI never writes | `queries/guard.py`, `ai/service.py` |
| AC5 confirmed, audited writes | `confirmations/service.py`, `audit/service.py` |
| AC6 real schema discovery | `schema/service.py`, `connections/connectors/discovery.py`, `ai/identifiers.py` |
| AC7 real, restorable backups | `backups/service.py`, `connections/connectors/dump.py` |
| AC8 reviewed migrations | `comparisons/service.py`, `confirmations/service.py` |
| AC9 connection grants, 404 not 403 | `connections/access.py` |
| AC10 one audit path | `audit/service.py` (every domain imports this, nothing writes `audit_log` directly) |
| AC11 engine parity | `connections/models.py` `CONNECTION_KINDS`; `connections/connectors/` dispatch; documented gaps in [security.md](security.md) and the README's Features table |
| AC12 live aggregates, real worker | `dashboard/service.py`, `monitoring/service.py`, `worker.py`, `cli.py` |
| AC13 real weak-config check | `connections/connectors/health.py`, `health_sql.py` |
| AC14 SDK parity | not shipped in 0.1.0 (see [docs/PLAN.md](../PLAN.md) Roadmap) |
