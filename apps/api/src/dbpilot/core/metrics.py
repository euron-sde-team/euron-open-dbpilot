"""Prometheus metrics. Each one is incremented at a real call site (named next to it), and
``tests/integration/system/test_metrics.py`` drives each of those paths against a live
server and reads the counters back from ``/metrics``. The API serves them on ``/metrics``;
the worker on ``WORKER_METRICS_PORT``."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

_LATENCY = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)

# core/middleware.py
HTTP_REQUESTS = Counter(
    "dbpilot_http_requests_total", "HTTP requests by route template", ["method", "route", "status"]
)
HTTP_SECONDS = Histogram(
    "dbpilot_http_request_seconds",
    "HTTP request latency by route template",
    ["route"],
    buckets=_LATENCY,
)
# connections/gateway.py: every operation that reached a connected database.
CONNECTOR_CALLS = Counter(
    "dbpilot_connector_calls_total",
    "Operations that reached a connected database (all through the audited gateway)",
    ["engine", "action", "outcome"],
)
CONNECTOR_SECONDS = Histogram(
    "dbpilot_connector_seconds",
    "Time spent inside a connected database per operation",
    ["engine", "action"],
    buckets=_LATENCY,
)
# queries/service.py
QUERIES = Counter(
    "dbpilot_queries_total",
    "Statements run or refused against connected databases",
    ["engine", "mode", "source", "status"],
)
QUERY_SECONDS = Histogram(
    "dbpilot_query_seconds",
    "Query duration inside the connected database",
    ["engine", "mode"],
    buckets=_LATENCY,
)
# queries/service.py, ai/service.py: refusals before any database is reached.
GUARD_REFUSALS = Counter(
    "dbpilot_guard_refusals_total",
    "Statements a guard refused before any database was reached",
    ["path", "code"],
)
GUARD_SECONDS = Histogram(
    "dbpilot_guard_seconds",
    "Time the SQL guard spends parsing and checking one statement",
    buckets=(0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1),
)
# ai/service.py
AI_GENERATIONS = Counter(
    "dbpilot_ai_generations_total",
    "AI assistant outputs, accepted or refused",
    ["kind", "outcome"],
)
AI_MODEL_SECONDS = Histogram(
    "dbpilot_ai_model_seconds", "Model call latency", ["provider"], buckets=_LATENCY
)
# confirmations/service.py, confirmations/router.py
CONFIRMATIONS = Counter(
    "dbpilot_confirmations_total",
    "Writes, migrations and restores through the two-step confirmation path",
    ["kind", "stage"],
)
# backups/service.py
BACKUPS = Counter(
    "dbpilot_backups_total",
    "Backups by engine, trigger and result",
    ["engine", "trigger", "status"],
)
BACKUP_SECONDS = Histogram(
    "dbpilot_backup_seconds", "Backup duration", ["engine"], buckets=_LATENCY
)
BACKUP_BYTES = Counter("dbpilot_backup_bytes_total", "Compressed bytes written by backups")
RESTORES = Counter("dbpilot_restores_total", "Restores by engine and result", ["engine", "status"])
# connections/service.py
CONNECTION_TESTS = Counter(
    "dbpilot_connection_tests_total", "Connection tests", ["engine", "outcome"]
)
# schema/service.py
SCHEMA_DISCOVERIES = Counter(
    "dbpilot_schema_discoveries_total", "Live schema discoveries", ["engine", "outcome"]
)
# schema/service.py, monitoring/service.py, dashboard/service.py
CACHE = Counter("dbpilot_cache_requests_total", "Cache lookups", ["cache", "result"])
DEPENDENCY_ERRORS = Counter(
    "dbpilot_dependency_errors_total", "Failed calls to a dependency", ["dependency", "operation"]
)
# audit/service.py
AUDIT_ENTRIES = Counter(
    "dbpilot_audit_entries_total", "Audit entries written, by action group", ["group"]
)
# worker.py
WORKER_TICKS = Counter("dbpilot_worker_ticks_total", "Worker loop iterations", ["outcome"])
SCHEDULED_BACKUPS = Counter(
    "dbpilot_scheduled_backups_total", "Scheduled backups the worker ran", ["status"]
)
