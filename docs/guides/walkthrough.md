# Walkthrough

A tour of the dashboard against the seeded demo stack (`make up && make demo`). Sign in at
[http://localhost:5810](http://localhost:5810) as `maya.chen@lumenlogistics.example`
(project owner); the password is the `DBPILOT_DEMO_PASSWORD` value `make demo` printed.

## Projects

After sign-in you land on **Projects**: every project you belong to, your role in each.
The seeded demo has one project, **Lumen Logistics**, with five connections (one per
engine) already registered and a history of queries, backups and a schema comparison, so
every screen looks alive from the first visit.

## Project overview

The project's dashboard: connection health cards (reachability, a weak-configuration
check per engine, cached 15 seconds), org-wide totals (connections, queries run this week,
pending confirmations, recent backups), and recent audit activity.

## Connections

**Connections** lists every connection you have access to (admins and owners see all of
them; a viewer only through a grant, per [security.md](../architecture/security.md)).
Opening one shows its config (never its password, by construction, AC1), its safety
limits (`max_rows`, `max_scan_rows`, `timeout_s`), its health card, and its grants. **New
connection** walks through engine-specific fields: host/port/database/username/password
for PostgreSQL/MySQL/SQL Server, a connection string plus auth source for MongoDB, a URL
for Redis, or a file upload for SQLite.

## Schema explorer

Inside a connection: tables/collections, columns and types, indexes, foreign keys, and
(for the SQL engines) an ER-style relationship view, all from the real schema snapshot
(AC6). Row preview samples real rows.

## SQL editor and the AI assistant

For a SQL-engine connection, the **Editor** tab: a SQL editor with syntax highlighting,
autocomplete seeded from the real schema, query history and saved queries. The AI panel
alongside it takes a natural-language prompt and generates one statement against the
real schema; it is shown with its guard verdict before you can run it. Ask something that
implies a write ("delete the oldest ten orders") and watch it refuse with no way around it
(AC4); ask a genuine question and run the generated `SELECT` like any other query.

MongoDB and Redis connections show a different, dedicated panel instead of a SQL editor: a
filter/aggregate builder for MongoDB, a key browser and read-only command list for Redis
(see [ADR-0001](../architecture/adr/0001-mongodb-redis-dedicated-panels.md)), consistent
with their own query model rather than a forced SQL abstraction.

## Marking a write

Type a statement that is not read-only (for example `update orders set status =
'shipped' where id = 7;`) and the editor offers **Mark as a write** instead of running it
directly. Confirming shows exactly what will run, generates a one-time token and a digest,
and only executing that same token and digest actually runs the statement (AC5). Check
**Audit** afterward: three new entries, `write.requested`, `write.confirmed`,
`write.executed`, with your name, the exact statement, the connection and the result.

## Backups

The project's **Backups** page: **Back up now** against any eligible connection
(PostgreSQL, MySQL, SQLite, SQL Server). Each backup lists its size, table/row counts and
integrity-check result. **Restore** goes through the identical two-step confirmation as a
write (AC7); a restore's result is verified against the backup's recorded row counts.
Recurring schedules are a real, working API (`POST /api/v1/projects/{id}/backup-schedules`,
run by `dbpilot worker`) a step ahead of the dashboard: the schedule UI itself is marked
"coming soon" in 0.1.0, so set one up through the API or `docs/api/openapi.yaml` until that
screen ships.

## Schema comparison

**Comparisons**: pick two connections (or two points in time on the same one) and see a
diff plus a generated migration script. **Run migration** goes through the same
confirmation flow as any other write; DBPilot never runs the script itself (AC8).

## Audit log

Every query run or refused, every AI generation (accepted or refused), every confirmation
step, backup, restore and migration run, and every connection-grant change, newest first,
filterable by action and keyset-paginated (AC10).

## Project settings and instance admin

**Settings**: project members and their roles, connection grants. **System**
(`GET /api/v1/system`) shows registered worker heartbeats, confirming the scheduled-backup
worker is a real registered process, not a phantom service: no heartbeat means scheduled
backups will not run, and the page says so plainly rather than silently showing nothing.
