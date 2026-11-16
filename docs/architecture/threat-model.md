# Threat model

Scope: DBPilot's own API, web app and worker, and the infrastructure
`infra/terraform` provisions with its default settings. Not in scope: a compromised host,
a compromised AWS account, leaked deployment secrets, or denial of service through
resource exhaustion of a deployment you operate yourself.

## Assets

- Connection credentials (sealed, never returned by the API).
- The connected databases themselves: the real worry is an unintended write or an
  unintended large read against someone else's production data.
- The audit log, as the record a team relies on to know who did what.
- Backup dump files (they contain the connected database's own data).
- Session tokens and the instance's own Postgres.

## Actors and capabilities

| Actor | Can do |
| --- | --- |
| Project viewer | Browse schema and run read-only queries on connections they have a grant on |
| Project viewer with `can_write` grant | Also mark a query as a write, subject to confirmation |
| Project admin/owner | Everything above on every connection in the project, plus manage connections and grants |
| The AI assistant | Generate a statement; never execute anything outside the guard, and never anything but a read |
| An attacker with no account | Nothing: every route requires a session |
| An attacker with a viewer account but no grant on a connection | Sees a 404 for that connection and everything derived from it |

## Threats and mitigations

| Threat | Mitigation |
| --- | --- |
| A crafted statement that parses as read-only to the guard but is not, at the database level | The connection itself is opened read-only by the engine wherever it supports one (AC3); see [security.md](security.md) |
| The AI assistant is tricked (via prompt injection in a table/column comment, or a crafted natural-language prompt) into generating a write | The guard refuses anything but one read-only statement, with no override, regardless of how the statement was produced (AC4) |
| The AI assistant fabricates a plausible-looking table or column that does not exist, and a generated query runs against it silently | `ai/identifiers.py` cross-checks every identifier against the real schema discovery result; an unknown identifier is a refusal (AC6) |
| A viewer with no grant enumerates connection ids to find out which ones exist | Every route answers 404 for a connection with no grant, identical to one that does not exist (AC9) |
| A write is executed by someone other than the person who requested it, or without them seeing what will run | The confirmation's token is single-use and only its hash is stored; `begin` requires both the token and a digest of the exact statement, so a confirmation id alone is not enough (AC5) |
| A restore silently loses or corrupts data | A restore is verified against the backup's recorded per-table row counts; integrity is checked at backup time and the restore path reuses the same connector-level dump/restore code as the backup (AC7) |
| A generated migration script runs itself | DBPilot never executes a comparison's migration script; running it requires the same confirmation flow as any other write (AC8) |
| A connection profile is used to reach the API container's own cloud metadata endpoint | `core/net.py` resolves and rejects metadata and link-local addresses, in every numeric form a resolver might accept, before connecting (not merely string-matching the hostname given) |
| An audit entry is skipped by a code path that reaches the connector layer a different way | `connections/gateway.py` is the only module permitted to open a customer connection (enforced by `tests/unit/connections/test_one_door.py`'s import-graph check), and it always writes the audit entry (AC10) |
| A cross-tenant data leak through a shared cache key | Every cache key is scoped by connection or project id; Redis holds no data that is the only copy of anything, and a wrong key would simply miss, not leak a cached peer's data |
| Credential theft from the instance's own Postgres | Secrets are sealed (envelope encryption) at rest; a raw database dump of DBPilot's own tables does not expose any connection's plaintext password |

## Explicit 0.1.0 limitations (documented, not silently dropped)

- **In-database role/privilege auditing** (what grants the target database itself has
  given its own users) is Roadmap; 0.1.0 reports what DBPilot itself enforces
  (connections, grants, the guard), not a full audit of the target database's own grants.
- **SQL Server's read-only guarantee is partly application-enforced** (nested,
  always-rolled-back transactions plus a nesting check), because the engine has no native
  read-only session flag; a `db_datareader` login closes this gap at the database level
  and the health card recommends it.
- **MongoDB and Redis have no read-only session concept at all**; their guarantee is that
  the connector objects exposed to the query path have no write method, not a database
  setting. A future driver bug that added one would need its own code review, not a
  database-level backstop, which is why 0.1.0 deliberately keeps those two connector
  modules small and reviewed (see [ADR-0001](adr/0001-mongodb-redis-dedicated-panels.md)).
- **The AI assistant's schema context is capped to a character budget**; a connection with
  an extremely large schema may see the assistant refuse to generate rather than guess
  with partial context (refused as `no_sql`, never guessed).
