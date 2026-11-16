# Security

## Read-only, twice over

Every query that is not explicitly marked as an intentional write is checked by two
independent mechanisms, so one bypassing the other is not enough to write:

1. **The SQL guard** (`apps/api/src/dbpilot/queries/guard.py`): parses the statement with
   `sqlglot` in the connection's dialect and accepts only exactly one read-only query.
   Refuses anything else: `INSERT`/`UPDATE`/`DELETE`, DDL, multiple statements, a
   statement it cannot parse.
2. **The database itself** (`connections/connectors/openers.py`), opened read-only wherever
   the engine supports it:
   - **PostgreSQL**: the session starts with `default_transaction_read_only=on` and the
     transaction is pinned `SET TRANSACTION READ ONLY` before anything else runs. Every
     statement goes through the extended query protocol (never the simple protocol, which
     a crafted `COMMIT; BEGIN READ WRITE; INSERT ...` string could otherwise escape
     through), which the server limits to exactly one statement.
   - **MySQL**: `SET SESSION TRANSACTION READ ONLY`; writes and DDL fail with error 1792.
     The driver never enables multi-statements.
   - **SQLite**: the file is a private copy opened with `mode=ro`, `PRAGMA query_only=1`,
     and SQLite's own authorizer refuses every non-read action (attach, write, DDL,
     pragmas that change anything) while the statement compiles.
   - **SQL Server** has no read-only session mode. DBPilot opens a nested transaction
     (implicit transactions on, `@@TRANCOUNT` checked after every statement) that is
     always rolled back, plus the guard's own re-serialization of the parse tree. Connect
     with a `db_datareader` login for a database-enforced guarantee; the health card flags
     a login that can write.
   - **MongoDB and Redis** have no read-only session flag: the connector handles opened
     here (`connectors/mongo.py`, `connectors/redis_source.py`) expose read methods only,
     with no generic command method a write could travel through.

An AI-generated statement never gets a second chance past the guard (acceptance criterion
AC4): a refusal there is final, with no flag, no approval and no "run anyway" anywhere in
0.1.0. See [ADR-0002](adr/0002-ai-never-writes.md).

## Limits a query cannot escape

Every connection carries its own `max_rows`, `max_scan_rows` and `timeout_s`. Before a
`SELECT` runs, its planner's own estimate is checked against `max_scan_rows` and refused
before execution if it is over (never "run first, cut later"); a result over `max_rows` is
truncated with `truncated: true` in the response, never silently dropped (AC2). The
statement itself is bounded by `timeout_s`, enforced by the connected engine.

## Credentials

A connection's password is sealed with envelope encryption bound to the connection's own
record id (`core/sealing.py`: `KEY_MODE=local` derives a key from `LOCAL_MASTER_KEY`;
`KEY_MODE=kms` calls AWS KMS Encrypt/Decrypt) and is never returned by any API response,
at any time, for any role (AC1). It is opened only inside the gateway, at the moment a
connection is actually used. The AI provider's own API key is sealed the same way.

## Tenants and roles

Organization and project membership is structural: every connection-scoped and
project-scoped table carries a foreign key to its project, and every query goes through a
scoped repository. Project roles are `owner`, `admin`, `viewer`; `owner`/`admin` see and
may use every connection in the project, a `viewer` only through a `connection_grants` row
(to them by name, or to every member). A connection a caller has no grant on, and
everything derived from it (schema, history, AI generations, backups, comparisons, health),
answers **404**, not 403, on every route (AC9, `connections/access.py`), the same
structural-isolation convention used by every other project in the Euron Open program: a
403 confirms the resource exists and is just forbidden, a 404 reveals nothing.

## Sessions and requests

Argon2 password hashing. Sessions are signed JWTs carrying a `session_version`; bumping a
user's `session_version` signs out every session at once (used on password change).
`revoked_sessions` additionally blocks a specific session id until it would have expired
anyway. Rate limits: `LOGIN_RPM_PER_IP` (default 20/min) on sign-in,
`CONNECTION_TEST_RPM` (default 30/min) on connection tests, both Redis sliding windows
(in-process when Redis is absent). `COOKIE_SECURE` and a real `CORS_ORIGINS` allowlist
(never `*`) are both enforced by a production boot guard that refuses to start otherwise
(`core/config.py`'s `Settings.guard()`).

## Outbound connections

DBPilot's own address validation refuses a connection whose host resolves to a link-local,
loopback-from-the-API-container-but-not-the-intended-database, or other metadata-style
address at connect time (`core/net.py`), so a connection profile cannot be used to reach
the API container's own cloud metadata endpoint.

## What reaches a model

The AI assistant sends the discovered schema (table and column names, types; sample values
only when a connection has `sample_values` explicitly turned on) and the user's prompt to
the configured model provider. It never sends a connection's credential, and a generated
statement is checked against the real discovered schema before it can be accepted
(`ai/identifiers.py`): a column or table name the schema never returned makes the
generation a refusal, not a guess (AC6).

## Audit

`audit/service.py` is the only code path that writes `audit_log`; every domain calls
through it rather than inserting its own row (AC10). A human-marked write, a migration run
and a restore each produce three entries: `*.requested` (the confirmation is created),
`*.confirmed` (the token and digest matched), `*.executed` (the statement actually ran,
with its result) (AC5, AC7, AC8). An AI generation is recorded whether accepted or refused.

## Operator checklist

- Set a real `LOCAL_MASTER_KEY` and `SESSION_SECRET` (32+ random characters, never a
  default) before running with `ENVIRONMENT=production`; the boot guard refuses to start
  otherwise.
- Set `COOKIE_SECURE=true` and a real `CORS_ORIGINS` allowlist in production.
- Prefer `KEY_MODE=kms` and `STORAGE=s3` on AWS; `infra/terraform` already provisions both
  for `aws-dev`/`aws-prod`.
- Grant connection access, and especially `can_write`, only to the people who genuinely
  need to run confirmed writes; review `connection_grants` periodically.
- Terminate TLS in front of the deployment; DBPilot's own app layer adds no transport
  encryption of its own.
- Connect to SQL Server with a `db_datareader` login where possible, for a
  database-enforced guarantee rather than DBPilot's application-level check alone.
