# Configuration

Every runtime setting comes from the environment and is validated once at boot by
`Settings` (`apps/api/src/dbpilot/core/config.py`). `make env` generates
`deploy/compose/.env` for local use with random secrets; it is gitignored, and this is the
one file an operator edits for a local deployment.

## Core

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `environment` | `ENVIRONMENT` | `development` | `development`, `test`, or `production`. `production` triggers the boot guard below. |
| `log_level` | `LOG_LEVEL` | `INFO` | |
| `cloud` | `DBPILOT_CLOUD` | `local` | `local`, `floci`, or `aws`. Informational: app code has no cloud-specific branch, since boto3/aioboto3 read `AWS_ENDPOINT_URL` the same way regardless. |
| `database_url` | `DATABASE_URL` | `postgresql+asyncpg://dbpilot:dbpilot@localhost:5842/dbpilot` | DBPilot's own database: connections, snapshots, query history, audit log. Never the database a connection points at. |
| `db_pool_size` / `db_max_overflow` / `db_pool_timeout_s` | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_S` | `10` / `20` / `10.0` | |

## Cache and KV

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `redis_url` | `REDIS_URL` | empty | Without it, the schema/health/dashboard caches and rate limits fall back to in-process memory, correct for a single replica. See [HLD.md](../architecture/HLD.md)'s caching table. |
| `memory_cache_max_bytes` | `MEMORY_CACHE_MAX_BYTES` | 64,000,000 | Bound on the in-process fallback cache's size. |
| `schema_cache_ttl_s` | `SCHEMA_CACHE_TTL_S` | `3600` | A connection's discovered schema, see [HLD.md](../architecture/HLD.md). |
| `schema_max_age_minutes` | `SCHEMA_MAX_AGE_MINUTES` | `60` | A snapshot older than this is rediscovered from the live database on the next read, even on a cache hit. |
| `health_cache_ttl_s` | `HEALTH_CACHE_TTL_S` | `15` | The per-connection health card response cache. |
| `dashboard_cache_ttl_s` | `DASHBOARD_CACHE_TTL_S` | `15` | The org-wide dashboard aggregates response cache. |
| `result_ttl_s` | `RESULT_TTL_S` | `3600` | How long a query result's rows stay in the KV store for pagination and CSV export. |

## Sessions and secrets

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `session_secret` | `SESSION_SECRET` | dev placeholder | Signs session JWTs. |
| `session_ttl_hours` | `SESSION_TTL_HOURS` | `12` | |
| `cookie_secure` | `COOKIE_SECURE` | `false` | Must be `true` in production (boot guard). |
| `cors_origins` | `CORS_ORIGINS` | `http://localhost:5810,http://127.0.0.1:5810` | Comma-separated (not JSON) in the environment. Must not contain `*` in production (boot guard). |
| `public_url` | `PUBLIC_URL` | `http://localhost:5810` | Must be `https://` in production (boot guard). |
| `login_rpm_per_ip` | `LOGIN_RPM_PER_IP` | `20` | Sign-in rate limit per IP. |
| `connection_test_rpm` | `CONNECTION_TEST_RPM` | `30` | Connection-test rate limit. |

## Credentials and encryption

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `key_mode` | `KEY_MODE` | `local` | `local` (AES-256-GCM from `LOCAL_MASTER_KEY`) or `kms` (AWS KMS Encrypt/Decrypt, bound to the record's id as encryption context). Both are implemented, unlike some sibling projects' `kms` mode. |
| `local_master_key` | `LOCAL_MASTER_KEY` | dev placeholder | The single most sensitive value in a DBPilot deployment when `key_mode=local`: anyone with it and the database can decrypt every sealed connection password. Must differ from `SESSION_SECRET` (boot guard). |
| `kms_key_id` | `KMS_KEY_ID` | `alias/dbpilot-local` | Used only when `key_mode=kms`. |

## Storage

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `storage` | `STORAGE` | `local` | `local` or `s3`. Must be `s3` in production (boot guard): the API and the worker must share the same backup files. |
| `objects_bucket` | `OBJECTS_BUCKET` | `dbpilot-local-objects` | |
| `local_storage_dir` | `LOCAL_STORAGE_DIR` | `./.data/objects` | |
| `work_dir` | `WORK_DIR` | `./.data/work` | Scratch space for dump/restore and SQLite private read-only copies. |
| `aws_region` / `aws_endpoint_url` / `s3_force_path_style` | `AWS_REGION` / `AWS_ENDPOINT_URL` / `S3_FORCE_PATH_STYLE` | `us-east-1` / unset / `false` | Set `AWS_ENDPOINT_URL` to Floci's address locally. |

## AI assistant

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `ai_provider` | `AI_PROVIDER` | `mock` | `mock` (the deterministic built-in model: tests, demo), `openai` (chat completions format, and any compatible gateway including the mock AI service), `anthropic`, or `none` (model calls off; explanations and diagnoses come from the syntax tree and rules alone). `mock` is refused in production (boot guard). |
| `ai_base_url` / `ai_api_key` / `ai_model` | `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | empty / empty / `dbpilot-mock` | `AI_API_KEY` is required in production for `openai`/`anthropic` (boot guard); `AI_MODEL` must not stay `dbpilot-mock` with a real provider. |
| `ai_timeout_s` | `AI_TIMEOUT_S` | `60.0` | |
| `prompt_schema_chars` | `PROMPT_SCHEMA_CHARS` | `24000` | The schema portion of a generation prompt stays under this character budget (relevant tables first). |

## Connections and safety limits

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `source_allow_private` | `SOURCE_ALLOW_PRIVATE` | `true` | Connected databases usually live on private networks. Link-local and instance-metadata addresses are never allowed, regardless of this setting (`core/net.py`). |
| `max_rows_ceiling` | `MAX_ROWS_CEILING` | `100000` | A ceiling no connection's own `max_rows` may exceed. |
| `max_timeout_ceiling_s` | `MAX_TIMEOUT_CEILING_S` | `600` | A ceiling no connection's own `timeout_s` may exceed. |
| `max_upload_bytes` | `MAX_UPLOAD_BYTES` | 200,000,000 | A SQLite file upload's size cap. |
| `max_body_bytes` | `MAX_BODY_BYTES` | 2,000,000 | Request body size cap. |
| `schema_max_tables` | `SCHEMA_MAX_TABLES` | `2000` | Discovery stops describing further tables/collections past this count. |

## Backups and confirmations

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `confirmation_ttl_s` | `CONFIRMATION_TTL_S` | `600` | A pending write/migration/restore confirmation must be completed within this window or it expires. |
| `backup_max_rows` | `BACKUP_MAX_ROWS` | 5,000,000 | A single logical backup's row ceiling. |
| `backup_stale_minutes` | `BACKUP_STALE_MINUTES` | `120` | A backup still `running` past this long belongs to a process that died; the worker's housekeeping marks it failed. |

## The worker (`dbpilot worker`)

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `worker_poll_interval_s` | `WORKER_POLL_INTERVAL_S` | `5.0` | How often it checks for due backup schedules and housekeeping. |
| `worker_batch` | `WORKER_BATCH` | `4` | How many due schedules it claims per loop. |
| `worker_metrics_port` | `WORKER_METRICS_PORT` | `9464` | Its `/metrics` endpoint, also its container health check. |

## The CLI and demo seed

| Setting | Env var | Default | Notes |
| --- | --- | --- | --- |
| `port` | `PORT` | `8080` | |
| `forwarded_allow_ips` | `FORWARDED_ALLOW_IPS` | `127.0.0.1` | `dbpilot serve` trusts `X-Forwarded-For` only from here (the load balancer's network on AWS). |
| `admin_password` | `DBPILOT_ADMIN_PASSWORD` | empty | Read by `dbpilot create-admin`. |
| `demo_password` | `DBPILOT_DEMO_PASSWORD` | `Demo-Owner-Pass-1` | Must match `scripts/gen-local-env.sh`'s own value or `make demo`/`make e2e`/`make qa` disagree on the password. Published, not a secret. |
| `demo_reader_password` | `DBPILOT_DEMO_READER_PASSWORD` | `Demo-Reader-Pass-1` | The demo read-only logins DBPilot's own connections use against the demo source databases. |
| `demo_source_pg_admin` / `demo_source_mysql_admin` / `demo_source_mssql_admin` / `demo_source_mongo_admin` / `demo_source_redis_url` | `DBPILOT_DEMO_SOURCE_PG_ADMIN` / `..._MYSQL_ADMIN` / `..._MSSQL_ADMIN` / `..._MONGO_ADMIN` / `..._REDIS_URL` | empty | Administrator URLs `dbpilot seed` uses to build the demo source databases and their read-only logins. Empty ones are skipped. |

## Local ports (`deploy/compose/.env`, `DBPILOT_*` prefix)

| Variable | Default |
| --- | --- |
| `DBPILOT_WEB_PORT` | 5810 |
| `DBPILOT_API_PORT` | 5830 |
| `DBPILOT_PG_PORT` | 5842 |
| `DBPILOT_REDIS_PORT` | 5879 |
| `DBPILOT_FLOCI_PORT` | 5866 |
| `DBPILOT_MAILPIT_PORT` | 5825 |
| `DBPILOT_MOCK_AI_PORT` | 5850 |
| `DBPILOT_SOURCE_PG_PORT` | 5843 |
| `DBPILOT_SOURCE_MYSQL_PORT` | 5806 |
| `DBPILOT_SOURCE_MSSQL_PORT` | 5807 |
| `DBPILOT_SOURCE_MONGO_PORT` | 5808 |
| `DBPILOT_SOURCE_REDIS_PORT` | 5809 |

All bound to `127.0.0.1` only. Never 3000, 3001, 5060, 4040 or 8081, reserved by other work
on this program.

## Production boot guard

`Settings.guard()` runs once at startup and, only when `environment=production`, refuses
to boot with any of: `SESSION_SECRET` under 32 characters or a known insecure default;
`key_mode=local` with an equally insecure `LOCAL_MASTER_KEY`, or `LOCAL_MASTER_KEY` equal to
`SESSION_SECRET`; `DBPILOT_CLOUD=aws` with `AWS_ENDPOINT_URL` still set; `COOKIE_SECURE` not
`true`; `STORAGE=local`; `AI_PROVIDER=mock`; `AI_PROVIDER` in (`openai`, `anthropic`) with
no `AI_API_KEY` or a model still named `dbpilot-mock`; `CORS_ORIGINS` containing `*`; or
`PUBLIC_URL` not starting with `https://`. Every problem found is listed in one
`RuntimeError` (`Settings.problems()`), so a misconfigured production deployment fails at
boot with a complete list rather than one error at a time.
