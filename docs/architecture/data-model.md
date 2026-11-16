# Data model

PostgreSQL 16, two Alembic revisions (`apps/api/src/dbpilot/migrations/versions/`). Every
table below `organizations`/`projects` carries a scoping foreign key; queries are always
scoped by it.

| Table | Holds | Notes |
| --- | --- | --- |
| `users` | People: email, password hash (argon2), name | |
| `organizations`, `org_members` | Tenants and membership | |
| `projects`, `project_members` | One workspace per team; roles `owner`, `admin`, `viewer` | Every connection, query and audit entry is scoped to a project |
| `revoked_sessions` | Signed-out session ids until they would expire | |
| `connections` | Kind (`postgresql`, `mysql`, `sqlite`, `mssql`, `mongodb`, `redis`), non-secret `config` (JSONB: host/port/database/username/ssl_mode), `secret_sealed` (envelope encrypted, never returned by the API), SQLite's `file_key`/`file_size`, safety limits (`max_rows`, `max_scan_rows`, `timeout_s`), `sample_values`, last-tested status, `server_version`, `schema_invalidated_at` | `ck_connections_kind`, `ck_connections_limits` constraints enforce valid values at the database, not just the API boundary |
| `connection_grants` | Who may use a connection besides the project's admins/owners: one `user_id`, or every member when `user_id` is null, and `can_write` (may prepare a confirmed write) | A partial unique index allows at most one grant per user per connection |
| `schema_snapshots` | A point-in-time discovered schema (tables/collections, columns, types, indexes, foreign keys, row-count estimates) per connection | Read by autocomplete, the AI assistant's context, the ER view and schema comparison |
| `query_runs` | Every statement run or refused: engine, `source` (`editor`, `ai`, `preview`, `saved`, `write`, `migration`), `mode` (`read`, `write`), the statement text, `status` (`succeeded`, `refused`, `failed`, `timeout`, `scan_limit`), row count, `truncated`, duration, the planner's `estimated_scan_rows`, actor | Indexed by project+time, connection+time, actor, and status, for the audit and history views |
| `saved_queries` | Named, reusable queries per project | |
| `ai_generations` | A natural-language prompt, the schema context sent, the generated statement, and whether it was accepted or refused (and why) | Retained whether accepted or refused, so a refusal is itself part of the audit trail |
| `confirmations` | A pending write, migration or restore: `kind` (`write`, `migration`, `restore`), the hashed one-time token, the `digest` of exactly what will run, `status` (`pending`, `executing`, `executed`, `failed`, `cancelled`, `expired`), expiry | The token itself is never stored, only its hash; `begin` must present both the token and the digest |
| `backups` | A backup run: engine, `trigger` (`manual`, `scheduled`), status, storage key, format, size, checksum, `tables` (per-table row counts at backup time), integrity-check result | A restore is verified against `tables`: the restored database's row counts must match |
| `backup_schedules` | A recurring backup schedule per connection, its cadence and retention | Claimed by the worker with `FOR UPDATE SKIP LOCKED` |
| `schema_comparisons` | Two schema snapshots (or a connection against a point in time), the generated diff and migration script | DBPilot never executes the script itself; running it goes through `confirmations` like any other write |
| `audit_log` | Append-only: every query run, AI generation, confirmation step, backup, restore, migration run and connection-grant change, with actor, action, target and details | The single write path every domain calls through (`audit/service.py`); nothing else writes this table |
| `worker_heartbeats` | One row per worker process: name, started/last-seen, version, stats | Surfaced on `GET /api/v1/system` |

Query result rows are never stored in Postgres: a `query_runs` row keeps only the shape
(columns, row count, truncation flag); the actual rows live in the key-value store for
`RESULT_TTL_S` (pagination and CSV export), then are gone. Uploaded SQLite files and backup
dump files live in object storage; their rows keep only a reference key, never the bytes.
