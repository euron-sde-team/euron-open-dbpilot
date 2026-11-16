"""Health and security probes: what the connected engine itself reports.

Every security check reads the engine's own setting through a real query (Postgres's
``ssl`` GUC, MySQL's ``require_secure_transport``, SQL Server's connection encryption,
MongoDB's authenticated users, Redis's ``requirepass``) and says which setting it read. A
check the login is not permitted to run reports ``unknown`` with the reason, never a
placeholder "pass" (acceptance criterion AC13). SQL engines are probed in
``health_sql.py``; MongoDB and Redis here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from dbpilot.connections.connectors.mongo import MongoReader
from dbpilot.connections.connectors.redis_source import RedisReader

Status = Literal["pass", "warn", "fail", "info", "unknown"]
T = TypeVar("T")


@dataclass
class Check:
    id: str
    title: str
    status: Status
    detail: str
    setting: str | None = None


@dataclass
class HealthReport:
    reachable: bool
    server_version: str = ""
    read_only_session: bool | None = None
    active_connections: int | None = None
    max_connections: int | None = None
    size_bytes: int | None = None
    uptime_s: int | None = None
    cache_hit_ratio: float | None = None
    slow_queries: list[dict[str, Any]] = field(default_factory=list)
    slow_queries_source: str | None = None
    slow_queries_note: str = ""
    index_advisories: list[dict[str, Any]] = field(default_factory=list)
    security: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def attempt[T](fn: Callable[[], T]) -> tuple[T | None, str | None]:
    """Run one probe; a refusal (missing privilege, disabled feature) is its message."""
    try:
        return fn(), None
    except Exception as exc:  # each driver raises its own types; the message is what matters
        return None, (str(exc).strip().splitlines() or [type(exc).__name__])[0][:240]


def tls_check(ssl_mode: str) -> Check:
    if ssl_mode in ("require", "verify-full"):
        return Check(
            "connection.tls",
            "Connection encryption",
            "pass",
            f"DBPilot connects with TLS (ssl_mode={ssl_mode})",
            f"ssl_mode={ssl_mode}",
        )
    return Check(
        "connection.tls",
        "Connection encryption",
        "warn",
        "DBPilot's connection to this database is not encrypted; credentials and results "
        "cross the network in plaintext",
        f"ssl_mode={ssl_mode}",
    )


def mongo_health(reader: MongoReader, ssl_mode: str) -> HealthReport:
    report = HealthReport(reachable=True, read_only_session=None)
    report.server_version = reader.server_version()
    status, err = attempt(reader.server_status)
    if status is not None:
        conns = status.get("connections") or {}
        report.active_connections = int(conns.get("current") or 0)
        available = conns.get("available")
        report.max_connections = (
            report.active_connections + int(available) if available is not None else None
        )
        report.uptime_s = int(status.get("uptime") or 0)
    else:
        report.notes.append(f"serverStatus is not permitted for this login: {err}")
    stats, err = attempt(reader.db_stats)
    if stats is not None:
        report.size_bytes = int(stats.get("dataSize") or 0) + int(stats.get("indexSize") or 0)
    level, err = attempt(reader.profile_level)
    if level:
        slow, err = attempt(reader.slow_operations)
        report.slow_queries = [
            {"query": str(op.get("command") or op.get("op"))[:300], "mean_ms": op.get("millis")}
            for op in slow or []
        ]
        report.slow_queries_source = "system.profile"
    else:
        report.slow_queries_note = (
            "The database profiler is off (profiling level 0), so MongoDB records no slow "
            "operations to show"
            if err is None
            else f"The profiler level could not be read: {err}"
        )
    conn_status, err = attempt(reader.connection_status)
    if conn_status is not None:
        auth = conn_status.get("authInfo") or {}
        users = auth.get("authenticatedUsers") or []
        roles = sorted({str(r.get("role")) for r in auth.get("authenticatedUserRoles") or []})
        if users:
            report.security.append(
                Check(
                    "mongodb.auth",
                    "Authentication",
                    "pass",
                    "The server required this login to authenticate",
                    f"authenticatedUsers={len(users)}",
                )
            )
        else:
            report.security.append(
                Check(
                    "mongodb.auth",
                    "Authentication",
                    "fail",
                    "Connected without authenticating: this server accepts anonymous "
                    "connections. Enable authorization",
                    "authenticatedUsers=0",
                )
            )
        writes = [
            r
            for r in roles
            if r in ("root", "readWrite", "dbOwner", "readWriteAnyDatabase", "dbAdmin")
        ]
        report.security.append(
            Check(
                "mongodb.privileges",
                "Login privileges",
                "warn" if writes else "pass",
                (
                    f"This login holds write roles ({', '.join(writes)}). DBPilot only ever "
                    "sends reads, but a login with the read role is the safer choice"
                )
                if writes
                else "This login holds no write roles",
                f"roles={','.join(roles) or 'none'}",
            )
        )
    else:
        report.security.append(
            Check("mongodb.auth", "Authentication", "unknown", f"connectionStatus failed: {err}")
        )
    report.security.append(tls_check(ssl_mode))
    report.security.append(
        Check(
            "readonly.enforcement",
            "Read-only enforcement",
            "info",
            "MongoDB has no read-only session mode. DBPilot exposes find, aggregate (read "
            "stages only), count and distinct, and refuses $out, $merge and server-side "
            "JavaScript before anything is sent",
        )
    )
    return report


def redis_health(reader: RedisReader, ssl_mode: str, authenticated: bool) -> HealthReport:
    report = HealthReport(reachable=True, read_only_session=None)
    info = reader.info()
    report.server_version = "Redis " + str(info.get("redis_version", ""))
    report.active_connections = int(info.get("connected_clients") or 0)
    report.max_connections = int(info["maxclients"]) if info.get("maxclients") else None
    report.size_bytes = int(info.get("used_memory") or 0)
    report.uptime_s = int(info.get("uptime_in_seconds") or 0)
    hits, misses = int(info.get("keyspace_hits") or 0), int(info.get("keyspace_misses") or 0)
    report.cache_hit_ratio = round(hits / (hits + misses), 4) if hits + misses else None
    slow = reader.slowlog(5)
    report.slow_queries = [
        {"query": s["command"], "mean_ms": s["duration_us"] / 1000} for s in slow
    ]
    report.slow_queries_source = "SLOWLOG"
    requirepass = reader.config_value("requirepass")
    if requirepass is None:
        report.security.append(
            Check(
                "redis.requirepass",
                "Password required",
                "pass" if authenticated else "unknown",
                "This login authenticated, so the server requires a password"
                if authenticated
                else "CONFIG is disabled or not permitted for this login, so requirepass "
                "could not be read",
            )
        )
    else:
        report.security.append(
            Check(
                "redis.requirepass",
                "Password required",
                "pass" if requirepass else "fail",
                "The server requires a password"
                if requirepass
                else "The server has no password (requirepass is empty): anyone who can "
                "reach the port can read and write every key",
                "requirepass=" + ("<set>" if requirepass else "<empty>"),
            )
        )
    protected = reader.config_value("protected-mode")
    if protected is not None:
        report.security.append(
            Check(
                "redis.protected_mode",
                "Protected mode",
                "pass" if protected == "yes" or requirepass else "warn",
                f"protected-mode is {protected}",
                f"protected-mode={protected}",
            )
        )
    report.security.append(tls_check(ssl_mode))
    report.security.append(
        Check(
            "readonly.enforcement",
            "Read-only enforcement",
            "info",
            "Redis has no read-only session mode. DBPilot runs only read commands from a "
            "fixed allowlist and refuses SET, DEL, FLUSHALL, EVAL, CONFIG and every other "
            "command before it is sent",
        )
    )
    return report
