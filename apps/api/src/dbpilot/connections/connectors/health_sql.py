"""Health and security probes for the SQL engines, read from each engine's own catalog and
settings on a read-only session. On PostgreSQL every probe runs in a savepoint, so one the
login may not run (``pg_stat_statements`` missing, say) cannot abort the others."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy.engine import Connection

from dbpilot.connections.connectors.health import Check, HealthReport, attempt
from dbpilot.connections.connectors.sql import confirm_read_only, raw_all, scalar, server_version

T = TypeVar("T")


def _probe[T](conn: Connection, fn: Callable[[], T]) -> tuple[T | None, str | None]:
    if conn.dialect.name != "postgresql":
        return attempt(fn)
    nested = conn.begin_nested()
    value, err = attempt(fn)
    if err is None:
        nested.commit()
    else:
        nested.rollback()
    return value, err


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _status_value(conn: Connection, sql: str) -> str | None:
    rows = raw_all(conn, sql)[1]
    return str(rows[0][1]) if rows else None


def sql_health(conn: Connection) -> HealthReport:
    kind = conn.dialect.name
    report = HealthReport(reachable=True, server_version=server_version(conn))
    report.read_only_session = confirm_read_only(conn) if kind != "mssql" else False
    {"postgresql": _postgres, "mysql": _mysql, "mssql": _mssql, "sqlite": _sqlite}[kind](
        conn, report
    )
    return report


def _postgres(conn: Connection, r: HealthReport) -> None:
    r.active_connections, _ = _probe(
        conn,
        lambda: _int(
            scalar(conn, "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
        ),
    )
    r.max_connections, _ = _probe(conn, lambda: _int(scalar(conn, "SHOW max_connections")))
    r.size_bytes, _ = _probe(
        conn, lambda: _int(scalar(conn, "SELECT pg_database_size(current_database())"))
    )
    r.uptime_s, _ = _probe(
        conn,
        lambda: _int(
            scalar(conn, "SELECT extract(epoch FROM now() - pg_postmaster_start_time())::bigint")
        ),
    )
    ratio, _ = _probe(
        conn,
        lambda: scalar(
            conn,
            "SELECT round((sum(blks_hit)::numeric / nullif(sum(blks_hit) + sum(blks_read), 0)), 4) "
            "FROM pg_stat_database WHERE datname = current_database()",
        ),
    )
    r.cache_hit_ratio = float(ratio) if ratio is not None else None
    has_pss, _ = _probe(
        conn,
        lambda: scalar(
            conn, "SELECT count(*) FROM pg_extension WHERE extname = 'pg_stat_statements'"
        ),
    )
    if has_pss:
        rows, err = _probe(
            conn,
            lambda: raw_all(
                conn,
                "SELECT left(query, 300), calls, round(mean_exec_time::numeric, 2) "
                "FROM pg_stat_statements WHERE dbid = (SELECT oid FROM pg_database "
                "WHERE datname = current_database()) ORDER BY mean_exec_time DESC LIMIT 5",
            )[1],
        )
        r.slow_queries_source = "pg_stat_statements"
        if rows is not None:
            r.slow_queries = [
                {"query": q, "calls": int(c), "mean_ms": float(m)} for q, c, m in rows
            ]
        else:
            r.slow_queries_note = f"pg_stat_statements is installed but not readable: {err}"
    else:
        r.slow_queries_note = (
            "pg_stat_statements is not installed in this database, so Postgres keeps no "
            "per-statement timings to show"
        )
    unused, _ = _probe(
        conn,
        lambda: raw_all(
            conn,
            "SELECT s.schemaname, s.relname, s.indexrelname, pg_relation_size(s.indexrelid) "
            "FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid "
            "WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary "
            "ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT 10",
        )[1],
    )
    for schema, table, index, size in unused or []:
        r.index_advisories.append(
            {
                "table": f"{schema}.{table}",
                "index": index,
                "kind": "unused_index",
                "reason": "The index has never been used since statistics were reset "
                f"({int(size):,} bytes)",
                "source": "pg_stat_user_indexes",
            }
        )
    seq, _ = _probe(
        conn,
        lambda: raw_all(
            conn,
            "SELECT schemaname, relname, seq_scan, seq_tup_read, coalesce(idx_scan, 0), n_live_tup "
            "FROM pg_stat_user_tables WHERE seq_scan > 0 AND n_live_tup > 10000 "
            "ORDER BY seq_tup_read DESC LIMIT 5",
        )[1],
    )
    for schema, table, scans, tuples, idx_scans, live in seq or []:
        if int(scans) > int(idx_scans):
            r.index_advisories.append(
                {
                    "table": f"{schema}.{table}",
                    "kind": "sequential_scans",
                    "reason": f"{int(scans):,} sequential scans read {int(tuples):,} rows of a "
                    f"{int(live):,}-row table, more often than any index was used",
                    "source": "pg_stat_user_tables",
                }
            )
    ssl, err = _probe(conn, lambda: str(scalar(conn, "SHOW ssl")))
    r.security.append(
        Check(
            "postgresql.ssl",
            "Server TLS (ssl setting)",
            "unknown" if ssl is None else ("pass" if ssl == "on" else "warn"),
            (
                f"Could not read the ssl setting: {err}"
                if ssl is None
                else (
                    "The server accepts TLS connections"
                    if ssl == "on"
                    else "The server has TLS turned off (ssl = off): no client can encrypt its "
                    "connection"
                )
            ),
            f"ssl={ssl}" if ssl is not None else None,
        )
    )
    enc, err = _probe(
        conn,
        lambda: raw_all(conn, "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()")[
            1
        ],
    )
    if enc:
        encrypted, version = enc[0]
        r.security.append(
            Check(
                "connection.tls",
                "Connection encryption",
                "pass" if encrypted else "warn",
                f"DBPilot's connection is encrypted ({version})"
                if encrypted
                else "DBPilot's connection to this database is not encrypted",
                f"pg_stat_ssl.ssl={encrypted}",
            )
        )
    pw, err = _probe(conn, lambda: str(scalar(conn, "SHOW password_encryption")))
    if pw is not None:
        r.security.append(
            Check(
                "postgresql.password_encryption",
                "Password hashing",
                "pass" if pw == "scram-sha-256" else "warn",
                "New passwords are stored as SCRAM-SHA-256"
                if pw == "scram-sha-256"
                else f"New passwords are stored as {pw}; use scram-sha-256",
                f"password_encryption={pw}",
            )
        )
    sup, err = _probe(
        conn, lambda: scalar(conn, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    )
    if sup is not None:
        r.security.append(
            Check(
                "postgresql.superuser",
                "Least privilege",
                "fail" if sup else "pass",
                "DBPilot connects as a superuser; give it a login without superuser rights"
                if sup
                else "The login is not a superuser",
                f"rolsuper={sup}",
            )
        )
    can_write, err = _probe(
        conn,
        lambda: scalar(
            conn,
            "SELECT coalesce(bool_or(has_table_privilege(c.oid, 'INSERT') OR "
            "has_table_privilege(c.oid, 'UPDATE') OR has_table_privilege(c.oid, 'DELETE')), false) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
            "AND n.nspname NOT LIKE 'pg_%'",
        ),
    )
    if can_write is not None:
        r.security.append(
            Check(
                "readonly.login",
                "Read-only login",
                "info" if can_write else "pass",
                "This login may modify tables. DBPilot still opens every read session "
                "read-only (Postgres enforces it); a read-only login adds a second lock"
                if can_write
                else "This login cannot modify any table",
                f"can_modify_tables={bool(can_write)}",
            )
        )


def _mysql(conn: Connection, r: HealthReport) -> None:
    r.active_connections, _ = _probe(
        conn, lambda: _int(_status_value(conn, "SHOW GLOBAL STATUS LIKE 'Threads_connected'"))
    )
    r.max_connections, _ = _probe(conn, lambda: _int(scalar(conn, "SELECT @@max_connections")))
    r.size_bytes, _ = _probe(
        conn,
        lambda: _int(
            scalar(
                conn,
                "SELECT COALESCE(SUM(data_length + index_length), 0) "
                "FROM information_schema.tables "
                "WHERE table_schema = DATABASE()",
            )
        ),
    )
    r.uptime_s, _ = _probe(
        conn, lambda: _int(_status_value(conn, "SHOW GLOBAL STATUS LIKE 'Uptime'"))
    )
    reads, _ = _probe(
        conn,
        lambda: _int(_status_value(conn, "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")),
    )
    requests, _ = _probe(
        conn,
        lambda: _int(
            _status_value(conn, "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read_requests'")
        ),
    )
    if reads is not None and requests:
        r.cache_hit_ratio = round(1 - reads / requests, 4)
    rows, err = _probe(
        conn,
        lambda: raw_all(
            conn,
            "SELECT LEFT(DIGEST_TEXT, 300), COUNT_STAR, ROUND(AVG_TIMER_WAIT / 1000000000, 2) "
            "FROM performance_schema.events_statements_summary_by_digest "
            "WHERE SCHEMA_NAME = DATABASE() AND DIGEST_TEXT IS NOT NULL "
            "ORDER BY AVG_TIMER_WAIT DESC LIMIT 5",
        )[1],
    )
    r.slow_queries_source = "performance_schema"
    if rows is not None:
        r.slow_queries = [
            {"query": str(q), "calls": int(c), "mean_ms": float(m)} for q, c, m in rows
        ]
    else:
        r.slow_queries_note = f"The statement digest could not be read: {err}"
    unused, _ = _probe(
        conn,
        lambda: raw_all(
            conn,
            "SELECT object_name, index_name FROM sys.schema_unused_indexes "
            "WHERE object_schema = DATABASE() LIMIT 10",
        )[1],
    )
    for table, index in unused or []:
        r.index_advisories.append(
            {
                "table": str(table),
                "index": str(index),
                "kind": "unused_index",
                "reason": "The index has not been used since the server started",
                "source": "sys.schema_unused_indexes",
            }
        )
    secure, err = _probe(conn, lambda: _int(scalar(conn, "SELECT @@require_secure_transport")))
    r.security.append(
        Check(
            "mysql.require_secure_transport",
            "Server requires TLS",
            "unknown" if secure is None else ("pass" if secure else "warn"),
            f"Could not read require_secure_transport: {err}"
            if secure is None
            else (
                "The server refuses unencrypted connections"
                if secure
                else "require_secure_transport is OFF: clients may connect without TLS"
            ),
            f"require_secure_transport={'ON' if secure else 'OFF'}" if secure is not None else None,
        )
    )
    cipher, err = _probe(conn, lambda: _status_value(conn, "SHOW SESSION STATUS LIKE 'Ssl_cipher'"))
    if cipher is not None or err is None:
        r.security.append(
            Check(
                "connection.tls",
                "Connection encryption",
                "pass" if cipher else "warn",
                f"DBPilot's connection is encrypted ({cipher})"
                if cipher
                else "DBPilot's connection to this database is not encrypted",
                f"Ssl_cipher={cipher or ''}",
            )
        )
    infile, err = _probe(conn, lambda: _int(scalar(conn, "SELECT @@local_infile")))
    if infile is not None:
        r.security.append(
            Check(
                "mysql.local_infile",
                "LOAD DATA LOCAL",
                "warn" if infile else "pass",
                "local_infile is ON: clients can make the server read their local files"
                if infile
                else "local_infile is OFF",
                f"local_infile={'ON' if infile else 'OFF'}",
            )
        )
    grants, err = _probe(
        conn, lambda: [str(g[0]) for g in raw_all(conn, "SHOW GRANTS FOR CURRENT_USER()")[1]]
    )
    if grants is not None:
        text = " ".join(grants).upper()
        admin = "ALL PRIVILEGES ON *.*" in text or " SUPER" in text
        writes = admin or any(p in text for p in ("INSERT", "UPDATE", "DELETE", "ALL PRIVILEGES"))
        r.security.append(
            Check(
                "mysql.privileges",
                "Least privilege",
                "fail" if admin else ("info" if writes else "pass"),
                "DBPilot connects with administrative privileges; give it a read-only login"
                if admin
                else (
                    "This login may modify data. DBPilot still opens every read session "
                    "READ ONLY (MySQL enforces it)"
                    if writes
                    else "This login can only read"
                ),
                "grants=" + ("administrative" if admin else "write" if writes else "read"),
            )
        )


def _mssql(conn: Connection, r: HealthReport) -> None:
    r.active_connections, err = _probe(
        conn,
        lambda: _int(
            scalar(conn, "SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1")
        ),
    )
    if err:
        r.notes.append(f"Session counts need VIEW SERVER STATE: {err}")
    r.max_connections, _ = _probe(conn, lambda: _int(scalar(conn, "SELECT @@MAX_CONNECTIONS")))
    r.size_bytes, _ = _probe(
        conn,
        lambda: _int(
            scalar(conn, "SELECT SUM(CAST(size AS bigint)) * 8 * 1024 FROM sys.database_files")
        ),
    )
    r.uptime_s, _ = _probe(
        conn,
        lambda: _int(
            scalar(
                conn,
                "SELECT DATEDIFF(second, sqlserver_start_time, SYSDATETIME()) "
                "FROM sys.dm_os_sys_info",
            )
        ),
    )
    rows, err = _probe(
        conn,
        lambda: raw_all(
            conn,
            "SELECT TOP 5 SUBSTRING(st.text, 1, 300), qs.execution_count, "
            "CAST(qs.total_elapsed_time / qs.execution_count / 1000.0 AS decimal(18, 2)) "
            "FROM sys.dm_exec_query_stats qs CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st "
            "WHERE st.dbid = DB_ID() ORDER BY 3 DESC",
        )[1],
    )
    r.slow_queries_source = "sys.dm_exec_query_stats"
    if rows is not None:
        r.slow_queries = [
            {"query": str(q), "calls": int(c), "mean_ms": float(m)} for q, c, m in rows
        ]
    else:
        r.slow_queries_note = f"Query statistics need VIEW SERVER STATE: {err}"
    missing, _ = _probe(
        conn,
        lambda: raw_all(
            conn,
            "SELECT TOP 10 d.statement, d.equality_columns, d.inequality_columns, "
            "s.avg_user_impact "
            "FROM sys.dm_db_missing_index_details d "
            "JOIN sys.dm_db_missing_index_groups g ON g.index_handle = d.index_handle "
            "JOIN sys.dm_db_missing_index_group_stats s ON s.group_handle = g.index_group_handle "
            "WHERE d.database_id = DB_ID() ORDER BY s.avg_user_impact DESC",
        )[1],
    )
    for statement, eq, ineq, impact in missing or []:
        cols = ", ".join(c for c in (eq, ineq) if c)
        r.index_advisories.append(
            {
                "table": str(statement),
                "columns": cols,
                "kind": "missing_index",
                "reason": f"SQL Server's optimizer estimates a {float(impact):.0f}% improvement",
                "source": "sys.dm_db_missing_index_details",
            }
        )
    enc, err = _probe(
        conn,
        lambda: scalar(
            conn, "SELECT encrypt_option FROM sys.dm_exec_connections WHERE session_id = @@SPID"
        ),
    )
    r.security.append(
        Check(
            "connection.tls",
            "Connection encryption",
            "unknown" if enc is None else ("pass" if str(enc).upper() == "TRUE" else "warn"),
            f"Could not read the connection's encryption: {err}"
            if enc is None
            else (
                "DBPilot's connection is encrypted"
                if str(enc).upper() == "TRUE"
                else "DBPilot's connection to this database is not encrypted"
            ),
            f"encrypt_option={enc}" if enc is not None else None,
        )
    )
    sysadmin, _ = _probe(conn, lambda: _int(scalar(conn, "SELECT IS_SRVROLEMEMBER('sysadmin')")))
    if sysadmin is not None:
        r.security.append(
            Check(
                "mssql.sysadmin",
                "Least privilege",
                "fail" if sysadmin else "pass",
                "DBPilot connects as a sysadmin; give it a db_datareader login"
                if sysadmin
                else "The login is not a sysadmin",
                f"IS_SRVROLEMEMBER(sysadmin)={sysadmin}",
            )
        )
    writes, err = _probe(
        conn,
        lambda: _int(
            scalar(
                conn,
                "SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT') + "
                "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE') + "
                "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE')",
            )
        ),
    )
    r.security.append(
        Check(
            "readonly.login",
            "Read-only login",
            "unknown" if writes is None else ("warn" if writes else "pass"),
            f"Could not read the login's permissions: {err}"
            if writes is None
            else (
                "SQL Server has no read-only session mode and this login can modify data. "
                "DBPilot's read sessions are rollback-only transactions running the SQL "
                "guard's own parse of each statement; connect with a db_datareader login "
                "for a database-enforced guarantee"
                if writes
                else "This login cannot modify data: the database enforces read-only"
            ),
            f"database_write_permissions={writes}" if writes is not None else None,
        )
    )


def _sqlite(conn: Connection, r: HealthReport) -> None:
    pages, _ = _probe(conn, lambda: _int(scalar(conn, "PRAGMA page_count")))
    size, _ = _probe(conn, lambda: _int(scalar(conn, "PRAGMA page_size")))
    if pages is not None and size is not None:
        r.size_bytes = pages * size
    journal, _ = _probe(conn, lambda: str(scalar(conn, "PRAGMA journal_mode")))
    r.slow_queries_note = "SQLite keeps no statement statistics"
    r.security.append(
        Check(
            "sqlite.file",
            "Local file",
            "info",
            "A SQLite database is a file: no network transport and no authentication, so "
            "anyone holding the file holds the data. DBPilot reads a private copy opened "
            "read-only (mode=ro, query_only and SQLite's authorizer)",
            f"journal_mode={journal}" if journal else None,
        )
    )
