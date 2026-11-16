"""Open a connected database. This module is the only code that creates a connection to a
customer's database, and only ``connections/gateway.py`` may import it (enforced by
``tests/unit/connections/test_one_door.py``), so every connection is opened by the audited
gateway.

Read-only is enforced by the database itself wherever the engine can enforce it, so a
statement that slipped past the SQL guard still cannot write (acceptance criterion AC3):

* PostgreSQL: the session starts with ``default_transaction_read_only=on`` and the
  transaction is pinned read-only before anything else runs (``SET TRANSACTION READ ONLY``
  then a first query, after which Postgres refuses to change the mode). Every statement
  DBPilot sends on it goes through the extended query protocol (``prepare=True`` or a
  server-side cursor), which the server limits to exactly one statement: a crafted
  ``COMMIT; BEGIN READ WRITE; INSERT ...`` string is refused by Postgres itself instead of
  escaping the read-only transaction (it does escape through the simple protocol, which is
  why DBPilot never uses it on a read-only session).
* MySQL: ``SET SESSION TRANSACTION READ ONLY``; writes and DDL fail with error 1792. The
  driver never enables multi-statements, and a connection runs one user statement.
* SQLite: the file is a private copy opened with ``mode=ro``, ``PRAGMA query_only=1``, and
  SQLite's own authorizer, which refuses every action that is not a read (attaching
  another file, writing, DDL, pragmas that change anything) while the statement compiles.
* SQL Server has no read-only session mode. DBPilot opens a nested transaction
  (``@@TRANCOUNT`` of at least 2, with implicit transactions on) that is always rolled
  back, checks after every statement that the nesting is intact, and runs the guard's own
  re-serialization of the parse tree (``queries/guard.py``), never the raw text. Connect
  with a ``db_datareader`` login for a database-enforced guarantee; the health card flags a
  login that can write (``connectors/probes.py``).
* MongoDB and Redis have no read-only session flag: the handles opened here expose read
  methods only (``connectors/mongo.py``, ``connectors/redis_source.py``), with no generic
  command method a write could travel through.

Write mode (``mode="write"``) exists for the confirmed-write path only (AC5): the gateway
opens it after the confirmation has been committed with its audit entry.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import pymongo
import redis
import sqlalchemy as sa
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.pool import NullPool

from dbpilot.connections.connectors.mongo import MongoReader
from dbpilot.connections.connectors.redis_source import RedisReader
from dbpilot.connections.connectors.sql import (
    DENIED_SQLITE_FUNCTIONS,
    MSSQL_NESTING,
    pg_execute,
)
from dbpilot.connections.connectors.targets import (
    APP_NAME,
    MongoTarget,
    RedisTarget,
    SqlTarget,
    connect_host,
)

Mode = Literal["read", "write"]
# PRAGMAs SQLAlchemy's reflection and DBPilot's probes read. Function-style ones take a
# table or index name; the others are allowed only in their reading form (no value).
_TABLE_PRAGMAS = frozenset(
    {"table_info", "table_xinfo", "index_list", "index_info", "index_xinfo", "foreign_key_list"}
)
_READ_PRAGMAS = frozenset(
    {
        "database_list",
        "table_list",
        "page_count",
        "page_size",
        "freelist_count",
        "journal_mode",
        "user_version",
        "schema_version",
        "encoding",
        "compile_options",
        "collation_list",
        "function_list",
        "quick_check",
        "query_only",
    }
)


def _sqlite_reader_authorizer(
    action: int, arg1: str | None, arg2: str | None, _db: str | None, _src: str | None
) -> int:
    """SQLite's own access check, called while each statement compiles: reads only."""
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        name = (arg2 or "").lower()
        return sqlite3.SQLITE_DENY if name in DENIED_SQLITE_FUNCTIONS else sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA:
        name = (arg1 or "").lower()
        if name in _TABLE_PRAGMAS or (name in _READ_PRAGMAS and arg2 is None):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_TRANSACTION:
        # BEGIN/COMMIT/ROLLBACK cannot write on a read-only file; the driver needs them.
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _sqlite_writer_authorizer(
    action: int, arg1: str | None, arg2: str | None, _db: str | None, _src: str | None
) -> int:
    """Confirmed writes may change the database, never reach another file."""
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in DENIED_SQLITE_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _engine(t: SqlTarget, timeout_s: int, mode: Mode) -> Engine:
    ms = max(int(timeout_s * 1000), 1)
    if t.kind == "postgresql":
        url = URL.create(
            "postgresql+psycopg",
            t.username or None,
            t.password,
            t.host,
            t.port or 5432,
            t.database or None,
        )
        options = f"-c statement_timeout={ms} -c idle_in_transaction_session_timeout={ms + 30_000}"
        if mode == "read":
            options = "-c default_transaction_read_only=on " + options
        args: dict[str, Any] = {
            "connect_timeout": 10,
            "options": options,
            "application_name": APP_NAME,
            "sslmode": t.ssl_mode or "prefer",
        }
        if t.address:
            args["hostaddr"] = t.address  # libpq connects here; TLS still verifies ``host``
        return sa.create_engine(url, poolclass=NullPool, connect_args=args, hide_parameters=True)
    if t.kind == "mysql":
        url = URL.create(
            "mysql+pymysql",
            t.username or None,
            t.password,
            connect_host(t),
            t.port or 3306,
            t.database or None,
        )
        args = {"connect_timeout": 10, "read_timeout": timeout_s + 15, "charset": "utf8mb4"}
        if t.ssl_mode in ("require", "verify-full"):
            args["ssl"] = {"check_hostname": t.ssl_mode == "verify-full"}
        return sa.create_engine(url, poolclass=NullPool, connect_args=args, hide_parameters=True)
    if t.kind == "mssql":
        url = URL.create(
            "mssql+pymssql",
            t.username or None,
            t.password,
            t.address or t.host,
            t.port or 1433,
            t.database or None,
        )
        args = {"login_timeout": 10, "timeout": timeout_s + 5, "appname": APP_NAME}
        return sa.create_engine(url, poolclass=NullPool, connect_args=args, hide_parameters=True)
    if t.kind == "sqlite":
        if not t.sqlite_path:
            raise ValueError("a SQLite connection needs its database file")
        path = Path(t.sqlite_path).resolve().as_posix()
        uri_mode = "ro" if mode == "read" else "rw"
        return sa.create_engine(
            f"sqlite:///file:{path}?mode={uri_mode}&uri=true",
            poolclass=NullPool,
            hide_parameters=True,
        )
    raise ValueError(f"unknown SQL connection kind {t.kind!r}")


def _prepare(conn: Connection, t: SqlTarget, timeout_s: int, mode: Mode, snapshot: bool) -> None:
    ms = max(int(timeout_s * 1000), 1)
    if t.kind == "postgresql":
        if mode == "read":
            # Pin this transaction read-only: after its first query Postgres refuses to
            # change the mode, whatever a later statement asks for.
            pg_execute(conn, "SET TRANSACTION READ ONLY")
            if snapshot:
                pg_execute(conn, "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            pg_execute(conn, "SELECT 1")
    elif t.kind == "mysql":
        if mode == "read":
            conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {ms}")
            if snapshot:
                conn.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
        else:
            conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {ms}")
    elif t.kind == "sqlite":
        raw = conn.connection.dbapi_connection
        assert isinstance(raw, sqlite3.Connection)
        if mode == "read":
            raw.execute("PRAGMA query_only = 1")
            raw.set_authorizer(_sqlite_reader_authorizer)
        else:
            raw.set_authorizer(_sqlite_writer_authorizer)
        deadline = time.monotonic() + timeout_s
        # Interrupt a statement that runs past the timeout (checked every 10k VM steps).
        raw.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    elif t.kind == "mssql":
        conn.exec_driver_sql(f"SET LOCK_TIMEOUT {ms}")
        if mode == "read":
            conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            # Any statement that finds no open transaction opens one, which the final
            # rollback undoes; the nesting means one COMMIT cannot end the outer one.
            conn.exec_driver_sql("SET IMPLICIT_TRANSACTIONS ON")
            while int(conn.exec_driver_sql("SELECT @@TRANCOUNT").scalar() or 0) < MSSQL_NESTING:
                conn.exec_driver_sql("BEGIN TRANSACTION")


@contextmanager
def open_sql(
    t: SqlTarget, timeout_s: int, *, mode: Mode = "read", snapshot: bool = False
) -> Iterator[Connection]:
    """A connection inside a transaction. Read mode always rolls back; write mode rolls
    back whatever the caller did not commit."""
    engine = _engine(t, timeout_s, mode)
    try:
        with engine.connect() as conn:
            _prepare(conn, t, timeout_s, mode, snapshot)
            try:
                yield conn
            finally:
                conn.rollback()
    finally:
        engine.dispose()


@contextmanager
def open_mongo(t: MongoTarget, timeout_s: int) -> Iterator[MongoReader]:
    # pymongo refuses any tls* option while TLS is off, so they are passed only with it.
    tls_options: dict[str, Any] = {"tls": False}
    if t.ssl_mode in ("require", "verify-full"):
        tls_options = {"tls": True, "tlsAllowInvalidHostnames": t.ssl_mode != "verify-full"}
    client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(
        host=connect_host(t),
        port=t.port,
        username=t.username or None,
        password=t.password or None,
        authSource=t.auth_source or "admin",
        **tls_options,
        directConnection=True,
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=10_000,
        socketTimeoutMS=(timeout_s + 5) * 1000,
        appname=APP_NAME,
        retryWrites=False,
        maxPoolSize=1,
    )
    try:
        yield MongoReader(client, t.database, timeout_s)
    finally:
        client.close()


@contextmanager
def open_redis(t: RedisTarget, timeout_s: int) -> Iterator[RedisReader]:
    client = redis.Redis(
        host=connect_host(t),
        port=t.port,
        db=t.db,
        username=t.username or None,
        password=t.password or None,
        ssl=t.ssl_mode in ("require", "verify-full"),
        ssl_cert_reqs="required" if t.ssl_mode == "verify-full" else "none",
        socket_timeout=timeout_s + 5,
        socket_connect_timeout=10,
        decode_responses=False,
        client_name=APP_NAME,
    )
    try:
        yield RedisReader(client)
    finally:
        client.close()
