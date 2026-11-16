# Operations

## Health and readiness

- `GET /healthz`: the process is up.
- `GET /readyz`: the process can reach its dependencies (Postgres, and Redis when
  configured).
- `GET /metrics`: Prometheus metrics, including `dbpilot_queries_total{engine,mode,source,status}`
  (every query run or refused), `dbpilot_guard_refusals_total{path,code}` (the SQL guard's
  refusals, separately for a human-typed and an AI-generated statement),
  `dbpilot_ai_generations_total{kind,outcome}`,
  `dbpilot_confirmations_total{kind,stage}`, `dbpilot_backups_total{engine,trigger,status}` and
  `dbpilot_backup_bytes_total`, `dbpilot_restores_total{engine,status}`,
  `dbpilot_connection_tests_total{engine,outcome}`, `dbpilot_schema_discoveries_total{engine,outcome}`,
  `dbpilot_cache_requests_total{cache,result}`, `dbpilot_dependency_errors_total{dependency,operation}`,
  `dbpilot_audit_entries_total{group}`, `dbpilot_worker_ticks_total{outcome}` and
  `dbpilot_scheduled_backups_total{status}` (the worker, on `WORKER_METRICS_PORT`, default
  9464, also its own health check).

Point your orchestrator's liveness probe at `/healthz` and readiness probe at `/readyz`;
restarting a process that fails `/healthz` is always safe, restarting one only failing
`/readyz` usually is not (it may just mean a dependency is briefly down).

## Logs

Structured JSON, one line per request, including a request id. Locally:

```sh
make logs SERVICE=api        # or web, worker, mock-ai, migrate, postgres, redis, floci, terraform, mailpit
make ps                      # stack status
```

## The dashboard summary and health cards

There is no separate rollup worker: the dashboard's org-wide totals (connections, queries
run, backups, pending confirmations) and each connection's health card are live Postgres
aggregate queries or a live probe of the connected engine (`dashboard/service.py`,
`monitoring/service.py`), behind a 15-second Redis (or in-process memory) response cache.
A cache miss costs one aggregate query or one health probe, never a scan of every row in
`query_runs`. The scheduled-backup worker (`dbpilot worker`) exists only for backup
schedules, confirmation expiry and its own heartbeat, never for these aggregates.

## Backups and restores

From the dashboard: the project's **Backups** page, **Back up now**. A restore is gated
behind the same two-step confirmation as any other write (token plus digest) and verified
against the backup's recorded per-table row counts afterward. Recurring schedules work
end to end (`dbpilot worker` claims and runs them) but the schedule UI itself is "coming
soon" in 0.1.0; create one through `POST /api/v1/projects/{id}/backup-schedules` until
then. See [walkthrough.md](walkthrough.md).

## Stopping the stack

```sh
make down
```

Stops every container **and deletes the stack's local Docker volumes** (DBPilot's own
Postgres, the five demo source databases, object storage, work directory, Floci data),
except the Terraform provider plugin cache, which survives so the next `make up` does not
re-download providers.

## Rotating secrets

- `SESSION_SECRET`: rotating it invalidates every existing dashboard session immediately
  (every session is a JWT signed with it).
- `LOCAL_MASTER_KEY` (when `key_mode=local`): rotating it requires re-sealing every
  `connections.secret_sealed` row under the new key; 0.1.0 has no automated migration for
  this yet. Treat a planned rotation as a manual, scripted migration task, and back up
  first. Rotating under `key_mode=kms` is simpler: KMS key rotation is handled by AWS and
  DBPilot's own ciphertext does not need re-sealing for an automatic key rotation.

## Known 0.1.0 operational limitations

- **In-database role/privilege auditing** (what the connected database itself grants its
  own users) is Roadmap; the health card and connection grants describe what DBPilot
  itself enforces.
- **No alerting beyond Prometheus metrics.** There is no built-in notification channel; wire
  `/metrics` into your own alerting stack.
- **The AI assistant's schema context is capped** (`PROMPT_SCHEMA_CHARS`, default 24,000
  characters); an extremely large schema may see the assistant refuse to generate rather
  than guess with partial context.

## Scaling

The API is stateless; run as many replicas as you like behind a load balancer, state
lives in Postgres and (optionally) Redis. Without Redis, the schema/health/dashboard
caches and rate limits are per-process, which is correct but not shared across replicas,
see [configuration.md](configuration.md)'s `redis_url` entry. The worker is safe to run as
multiple replicas (Postgres `FOR UPDATE SKIP LOCKED` on backup schedules). Size
`db_pool_size` and `db_max_overflow` so `api replicas x (pool + overflow)` stays under your
database's `max_connections`.
