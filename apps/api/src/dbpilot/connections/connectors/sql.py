"""Operations on an open SQL connection: version, read-only confirmation, scan estimates,
capped query execution and confirmed write statements. Synchronous: the gateway runs them
in a worker thread on a connection it opened (these functions never open one).

User SQL always goes through a raw DBAPI cursor with no parameters, so drivers never treat a
``%`` in the query as a placeholder. On PostgreSQL it always uses the extended query
protocol (one statement, enforced by the server).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import defusedxml.ElementTree as SafeXml
import sqlalchemy as sa
from sqlalchemy.engine import Connection

from dbpilot.connections.connectors.values import kind_of, to_json_value

# SQLite functions that read or write files or load code. The authorizer refuses them.
DENIED_SQLITE_FUNCTIONS = frozenset(
    {"load_extension", "writefile", "readfile", "edit", "fts3_tokenizer"}
)


# SQL Server read sessions run inside at least this many nested transactions (openers.py),
# so one smuggled COMMIT cannot end the transaction that is rolled back at the end.
MSSQL_NESTING = 2


class QueryTimeoutError(Exception):
    """The database stopped the statement at the connection's timeout."""


class ScanLimitError(Exception):
    """The query would read more rows than the connection allows."""

    def __init__(self, estimated: int, limit: int) -> None:
        super().__init__(f"estimated {estimated} rows scanned, limit {limit}")
        self.estimated = estimated
        self.limit = limit


class ScanUnknownError(Exception):
    """The database's plan says nothing DBPilot can bound the query by, so it is not run."""


class TransactionTamperedError(Exception):
    """A statement on a SQL Server read session changed the transaction DBPilot opened."""


class WriteStoppedError(Exception):
    """A write ran past its deadline and its caller stopped waiting: it was rolled back
    before committing (MySQL schema changes that already ran stay applied)."""


def never_stopped() -> bool:
    return False


def _raw(conn: Connection) -> Any:
    raw = conn.connection.dbapi_connection
    assert raw is not None
    return raw


def pg_execute(conn: Connection, sql: str) -> None:
    """One statement through the extended protocol: Postgres refuses a second one."""
    cur = _raw(conn).cursor()
    try:
        cur.execute(sql, prepare=True)
    finally:
        cur.close()


def raw_all(conn: Connection, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Every row of a short statement (EXPLAIN, a probe), through a raw cursor."""
    cur = _raw(conn).cursor()
    try:
        if conn.dialect.name == "postgresql":
            cur.execute(sql, prepare=True)
        else:
            cur.execute(sql)
        names = [str(d[0]) for d in cur.description or []]
        return names, [tuple(r) for r in cur.fetchall()]
    finally:
        cur.close()


def scalar(conn: Connection, sql: str) -> Any:
    rows = raw_all(conn, sql)[1]
    return rows[0][0] if rows and rows[0] else None


def mssql_nesting_intact(conn: Connection) -> bool:
    """After a statement on a SQL Server read session: the transactions DBPilot opened are
    all still open (a statement that committed or rolled back would have closed one)."""
    return int(conn.exec_driver_sql("SELECT @@TRANCOUNT").scalar() or 0) >= MSSQL_NESTING


def server_version(conn: Connection) -> str:
    sql = {
        "postgresql": "SELECT version()",
        "mysql": "SELECT VERSION()",
        "mssql": "SELECT @@VERSION",
        "sqlite": "SELECT 'SQLite ' || sqlite_version()",
    }[conn.dialect.name]
    return str(scalar(conn, sql) or "").splitlines()[0][:200]


def confirm_read_only(conn: Connection) -> bool:
    """Ask the database whether this session is read-only (SQL Server cannot say: its
    read sessions are rollback-only transactions, reported as False)."""
    kind = conn.dialect.name
    if kind == "postgresql":
        return str(scalar(conn, "SHOW transaction_read_only")) == "on"
    if kind == "mysql":
        return str(scalar(conn, "SELECT @@session.transaction_read_only")) == "1"
    if kind == "sqlite":
        return str(scalar(conn, "PRAGMA query_only")) == "1"
    return False


def is_timeout(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "statement timeout",
            "canceling statement due to",
            "maximum statement execution time exceeded",
            "3024",
            "interrupted",
            "lock request time out",
            "timeout expired",
        )
    )


# ─── Scan estimates ──────────────────────────────────────────────────────────


def _relationship(node_type: str, child: dict[str, Any], index: int) -> str:
    """How a child plan runs under its parent (EXPLAIN names it; by position otherwise)."""
    named = child.get("Parent Relationship")
    if named:
        return str(named)
    if node_type == "Nested Loop":
        return "Outer" if index == 0 else "Inner"
    return ""


def _pg_plan_rows(conn: Connection, node: dict[str, Any], loops: float = 1.0) -> float:
    """Rows a PostgreSQL plan reads. A sequential scan reads its whole relation, not only
    the rows it keeps; the inner side of a nested loop runs once per outer row, and a
    correlated subquery (a SubPlan) once per row of the node it hangs from."""
    rows = 0.0
    node_type = str(node.get("Node Type", ""))
    if "Scan" in node_type and node.get("Relation Name"):
        planned = float(node.get("Plan Rows") or 0)
        if node_type == "Seq Scan":
            total = conn.execute(
                sa.text(
                    "SELECT reltuples::bigint FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid = c.relnamespace WHERE c.relname = :rel AND n.nspname = :schema"
                ),
                # VERBOSE plans name the schema; without it every scan would look "public".
                {"rel": node["Relation Name"], "schema": node.get("Schema", "public")},
            ).scalar()
            planned = max(planned, float(total or 0))
        rows += planned * loops
    children = node.get("Plans", []) or []
    relationships = [_relationship(node_type, c, i) for i, c in enumerate(children)]
    outer_rows = next(
        (
            float(c.get("Plan Rows") or 1)
            for c, r in zip(children, relationships, strict=True)
            if r == "Outer"
        ),
        1.0,
    )
    for child, relationship in zip(children, relationships, strict=True):
        times = 1.0
        if node_type == "Nested Loop" and relationship == "Inner":
            times = max(outer_rows, 1.0)
        elif relationship == "SubPlan":
            times = max(float(node.get("Plan Rows") or 1), 1.0)
        rows += _pg_plan_rows(conn, child, loops * times)
    return rows


def _mysql_plan_rows(names: list[str], rows: list[tuple[Any, ...]]) -> int:
    """Rows a MySQL EXPLAIN reads. Within one SELECT, each table is read once per row the
    tables before it produce (``rows`` is per read, ``filtered`` the share kept)."""
    if "rows" not in names:
        raise ScanUnknownError("the database's plan has no row estimate")
    at_rows = names.index("rows")
    at_id = names.index("id") if "id" in names else None
    at_filtered = names.index("filtered") if "filtered" in names else None
    total = 0.0
    prefix: dict[Any, float] = {}
    for r in rows:
        select_id = r[at_id] if at_id is not None else None
        examined = float(r[at_rows] or 0)
        before = prefix.get(select_id, 1.0)
        total += before * examined
        kept = float(r[at_filtered]) / 100 if at_filtered is not None and r[at_filtered] else 1.0
        prefix[select_id] = max(before * examined * kept, 1.0)
    return int(total)


def _mssql_plan_rows(xml_text: str) -> int:
    """Rows a SQL Server SHOWPLAN reads: for every scan, the object's cardinality, for
    every seek or lookup, the estimated rows read; each times its estimated executions
    (the inner side of a nested loop runs once per outer row)."""
    root = SafeXml.fromstring(xml_text)
    total = 0.0
    for el in root.iter():
        if not el.tag.endswith("RelOp"):
            continue
        op = el.get("PhysicalOp", "")
        executions = (
            1.0 + float(el.get("EstimateRebinds") or 0) + float(el.get("EstimateRewinds") or 0)
        )
        if "Scan" in op:
            per = float(el.get("TableCardinality") or el.get("EstimatedRowsRead") or 0)
        elif "Seek" in op or "Lookup" in op:
            per = float(el.get("EstimatedRowsRead") or el.get("EstimateRows") or 0)
        else:
            continue
        total += per * executions
    return int(total)


def estimate_scan(conn: Connection, sql: str, tables: list[str], aliases: dict[str, str]) -> int:
    """Rows the database expects to read for ``sql``, from its own planner. EXPLAIN without
    ANALYZE plans the query and runs nothing; SQL Server's SHOWPLAN does the same.

    There is no unbounded fallback: when the database cannot plan the query (it does not
    parse, or this login may not EXPLAIN it), its error propagates and the query does not
    run; when the plan carries no estimate, ScanUnknownError."""
    kind = conn.dialect.name
    if kind == "postgresql":
        raw = raw_all(conn, f"EXPLAIN (FORMAT JSON, VERBOSE) {sql}")[1][0][0]
        plan = raw if isinstance(raw, list) else json.loads(str(raw))
        return int(_pg_plan_rows(conn, plan[0]["Plan"]))
    if kind == "mysql":
        return _mysql_plan_rows(*raw_all(conn, f"EXPLAIN {sql}"))
    if kind == "mssql":
        conn.exec_driver_sql("SET SHOWPLAN_XML ON")
        try:
            rows = raw_all(conn, sql)[1]
        finally:
            conn.exec_driver_sql("SET SHOWPLAN_XML OFF")
        if not rows:
            raise ScanUnknownError("the database returned no plan")
        return _mssql_plan_rows(str(rows[0][0]))
    if kind == "sqlite":
        details = [str(r[-1]) for r in raw_all(conn, f"EXPLAIN QUERY PLAN {sql}")[1]]
        quote = conn.dialect.identifier_preparer.quote
        wanted = {t.lower().split(".")[-1] for t in tables}
        total = 0
        for d in details:
            words = d.split()
            if len(words) >= 2 and words[0] == "SCAN":
                word = words[2] if words[1] == "TABLE" and len(words) > 2 else words[1]
                name = aliases.get(word.lower(), word)
                if name.lower() in wanted:
                    n = scalar(conn, f"SELECT max(rowid) FROM {quote(name)}")
                    total += int(n or 0)
        return total
    raise ScanUnknownError(f"no scan estimate for {kind}")


# ─── Execution ───────────────────────────────────────────────────────────────


@dataclass
class ResultColumn:
    name: str
    kind: str  # number, text, date, datetime, boolean, other


@dataclass
class QueryResult:
    columns: list[ResultColumn]
    rows: list[list[Any]]
    truncated: bool
    duration_ms: float


def run_query(conn: Connection, sql: str, max_rows: int) -> QueryResult:
    """Run ``sql`` (already guarded) and fetch at most ``max_rows`` rows, never pulling a
    large result whole: PostgreSQL reads through a server-side cursor (DECLARE accepts only
    one SELECT), MySQL and SQL Server cap the rows the server sends, SQLite steps lazily.
    ``truncated`` says whether more rows existed."""
    started = time.perf_counter()
    kind = conn.dialect.name
    try:
        if kind == "mysql":
            conn.exec_driver_sql(f"SET SESSION sql_select_limit = {int(max_rows) + 1}")
        if kind == "mssql":
            conn.exec_driver_sql(f"SET ROWCOUNT {int(max_rows) + 1}")
        raw = _raw(conn)
        cur = (
            raw.cursor(name=f"dbp_{uuid.uuid4().hex[:12]}")
            if kind == "postgresql"
            else raw.cursor()
        )
        try:
            cur.execute(sql)
            names = [str(d[0]) for d in cur.description or []]
            fetched = cur.fetchmany(max_rows + 1) if cur.description else []
        finally:
            cur.close()
        if kind == "mssql":
            conn.exec_driver_sql("SET ROWCOUNT 0")
            if not mssql_nesting_intact(conn):
                raise TransactionTamperedError(
                    "The statement changed the transaction DBPilot opened; it was rolled back"
                )
    except (TransactionTamperedError, QueryTimeoutError):
        raise
    except Exception as exc:
        if is_timeout(exc):
            raise QueryTimeoutError(str(exc)) from exc
        raise
    truncated = len(fetched) > max_rows
    raw_rows = [list(r) for r in fetched[:max_rows]]
    columns = [
        ResultColumn(name=n, kind=kind_of([r[i] for r in raw_rows])) for i, n in enumerate(names)
    ]
    rows = [[to_json_value(v) for v in r] for r in raw_rows]
    return QueryResult(
        columns=columns,
        rows=rows,
        truncated=truncated,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )


@dataclass
class StatementOutcome:
    index: int
    rows_affected: int
    duration_ms: float


def run_statements(
    conn: Connection, statements: list[str], stopped: Callable[[], bool] = never_stopped
) -> list[StatementOutcome]:
    """Run confirmed write statements in order on a write-mode connection, in one
    transaction wherever the engine has transactional DDL (MySQL commits each DDL statement
    on its own, which the confirmation says up front). The caller commits them together
    (``gateway.write``); an error propagates and the connection rolls back. ``stopped`` is
    asked before each statement: once the caller has stopped waiting, nothing more runs."""
    kind = conn.dialect.name
    raw = _raw(conn)
    out: list[StatementOutcome] = []
    for i, sql in enumerate(statements):
        if stopped():
            raise WriteStoppedError(f"stopped before statement {i + 1}: the time limit passed")
        started = time.perf_counter()
        cur = raw.cursor()
        try:
            if kind == "postgresql":
                # Extended protocol: exactly the one statement that was confirmed runs; a
                # second command hidden in its text is refused by the server.
                cur.execute(sql, prepare=True)
            else:
                cur.execute(sql)
            affected = int(cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0)
        finally:
            cur.close()
        out.append(StatementOutcome(i, affected, round((time.perf_counter() - started) * 1000, 1)))
    return out


def commit(conn: Connection) -> None:
    """Commit everything on ``conn``: SQLAlchemy's transaction, then the driver's own
    connection (statements on a raw cursor open a transaction SQLAlchemy may not know of;
    on PostgreSQL its commit() alone would be a silent no-op that lets the close roll the
    write back)."""
    raw = _raw(conn)
    if conn.in_transaction():
        conn.commit()
    raw.commit()
