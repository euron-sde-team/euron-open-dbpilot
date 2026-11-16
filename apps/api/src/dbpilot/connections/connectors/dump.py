"""Logical backup and restore for the SQL engines, through the connector session: never a
shell-out to ``pg_dump``, ``mysqldump`` or any other program.

A dump is a gzip file of JSON lines (format ``dbpilot-dump/1``): a header, the DDL the
engine's own SQLAlchemy dialect compiles from the reflected schema (schemas, enum types,
tables without foreign keys, then indexes, foreign keys and views), the sequence positions
to restore, and the rows of each table in foreign key order, in batches, with lossless
value tags (``values.encode_value``). It is read on a consistent snapshot where the engine
offers one (PostgreSQL ``REPEATABLE READ``, MySQL ``WITH CONSISTENT SNAPSHOT``).

Names come from the source database, so they are never trusted as SQL: every identifier in
the DDL is quoted by the dialect's own identifier preparer, every literal is escaped, and
sequence positions are stored as names, set at restore time with bound parameters. On
PostgreSQL each DDL line is replayed through the extended query protocol, which runs one
statement at most.

A restore replays it on a write-capable connection to a database that holds none of the
dumped tables: tables, then rows, then indexes and constraints, then views, then sequence
positions, and finally counts every table's rows against the dump (AC7). It does not
commit: the gateway commits a write only while its caller is still waiting for it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.schema import AddConstraint, CreateIndex, CreateTable

from dbpilot.connections.connectors.discovery import schemas_of
from dbpilot.connections.connectors.sql import (
    WriteStoppedError,
    never_stopped,
    pg_execute,
    server_version,
)
from dbpilot.connections.connectors.values import decode_value, encode_value

FORMAT = "dbpilot-dump/1"
BATCH = 1000
_DEFINER = re.compile(r"\s*(ALGORITHM\s*=\s*\w+\s*)?DEFINER\s*=\s*\S+(\s+SQL SECURITY \w+)?", re.I)


class BackupTooLargeError(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"the database holds more than {limit:,} rows, the backup limit")
        self.limit = limit


@dataclass
class DumpSummary:
    engine: str
    server_version: str
    tables: dict[str, int] = field(default_factory=dict)
    views: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(self.tables.values())


def _key(t: sa.Table) -> str:
    return f"{t.schema}.{t.name}" if t.schema else t.name


def _ddl(conn: Connection, element: Any) -> str:
    return str(element.compile(dialect=conn.dialect)).strip()


def _qualified(conn: Connection, schema: str | None, name: str) -> str:
    q = conn.dialect.identifier_preparer
    return f"{q.quote(schema)}.{q.quote(name)}" if schema else q.quote(name)


def _quoted(conn: Connection, t: sa.Table) -> str:
    return _qualified(conn, t.schema, t.name)


def _literal(value: str) -> str:
    """A SQL string literal (standard quoting: PostgreSQL and SQL Server read ``\\`` as an
    ordinary character in it)."""
    return "'" + value.replace("'", "''") + "'"


def _create_schema(conn: Connection, schema: str) -> str:
    q = conn.dialect.identifier_preparer
    if conn.dialect.name == "mssql":
        # CREATE SCHEMA must be alone in its batch, hence EXEC; its text is a literal too.
        create = f"CREATE SCHEMA {q.quote_identifier(schema)}"
        return f"IF SCHEMA_ID(N{_literal(schema)}) IS NULL EXEC(N{_literal(create)})"
    return f"CREATE SCHEMA IF NOT EXISTS {q.quote_identifier(schema)}"


def set_sequence(conn: Connection, schema: str | None, table: str, column: str) -> None:
    """Move a serial column's sequence past the restored rows (PostgreSQL)."""
    target = _qualified(conn, schema, table)
    col = conn.dialect.identifier_preparer.quote(column)
    conn.execute(
        sa.text(
            f"SELECT setval(pg_get_serial_sequence(:target, :column), "
            f"COALESCE(MAX({col}), 1), MAX({col}) IS NOT NULL) FROM {target}"
        ),
        {"target": target, "column": column},
    )


def _copy_column(c: sa.Column[Any], kind: str) -> tuple[sa.Column[Any], bool]:
    """A clean copy of a reflected column (no foreign keys attached); True when it carries
    an auto-generated key whose sequence position must be restored."""
    default = str(c.server_default.arg) if c.server_default is not None else None  # type: ignore[attr-defined]
    args: list[Any] = []
    serial = False
    autoincrement: Any = c.autoincrement
    if kind == "postgresql" and default and "nextval(" in default:
        default, serial, autoincrement = None, True, True  # recreated as SERIAL
    if c.identity is not None:
        serial = True
        args.append(
            sa.Identity(always=False, start=c.identity.start, increment=c.identity.increment)
        )
    if c.computed is not None:
        args.append(sa.Computed(c.computed.sqltext, persisted=c.computed.persisted))
    column = sa.Column(
        c.name,
        c.type,
        *args,
        nullable=c.nullable,
        server_default=sa.text(default) if default is not None else None,
        autoincrement=autoincrement,
    )
    return column, serial


def _reflect(conn: Connection, schema: str | None) -> list[sa.Table]:
    md = sa.MetaData()
    inspector = sa.inspect(conn)
    wanted: list[sa.Table] = []
    for s in schemas_of(conn, schema):
        shown = s if conn.dialect.name in ("postgresql", "mssql") else None
        for name in inspector.get_table_names(schema=s):
            wanted.append(sa.Table(name, md, schema=shown, autoload_with=conn))
    keys = {_key(t) for t in wanted}
    return [t for t in md.sorted_tables if _key(t) in keys]


def _lines(
    conn: Connection, tables: list[sa.Table], summary: DumpSummary, schema: str | None
) -> Iterator[dict[str, Any]]:
    kind = conn.dialect.name
    md2 = sa.MetaData()
    copies: dict[str, sa.Table] = {}
    serials: dict[str, list[str]] = {}
    pk_sets: dict[str, list[frozenset[str]]] = {}
    for schema in sorted({t.schema for t in tables if t.schema}):
        if (kind == "postgresql" and schema != "public") or (kind == "mssql" and schema != "dbo"):
            yield {"t": "ddl", "phase": "schemas", "sql": _create_schema(conn, schema)}
    enums: dict[str, Any] = {}
    for t in tables:
        columns, serial_cols = [], []
        for c in t.columns:
            copy, serial = _copy_column(c, kind)
            columns.append(copy)
            if serial:
                serial_cols.append(c.name)
            enum_name = getattr(c.type, "name", None)
            if kind == "postgresql" and isinstance(c.type, sa.Enum) and enum_name:
                enums[enum_name] = c.type
        extra: list[Any] = []
        if t.primary_key.columns:
            extra.append(
                sa.PrimaryKeyConstraint(
                    *[c.name for c in t.primary_key.columns],
                    name=t.primary_key.name if isinstance(t.primary_key.name, str) else None,
                )
            )
        sets = [frozenset(c.name for c in t.primary_key.columns)]
        for con in t.constraints:
            if isinstance(con, sa.UniqueConstraint):
                extra.append(sa.UniqueConstraint(*[c.name for c in con.columns], name=con.name))
                sets.append(frozenset(c.name for c in con.columns))
            elif isinstance(con, sa.CheckConstraint) and con.sqltext is not None:
                extra.append(sa.CheckConstraint(sa.text(str(con.sqltext)), name=con.name))
        copies[_key(t)] = sa.Table(t.name, md2, *columns, *extra, schema=t.schema)
        serials[_key(t)] = serial_cols
        pk_sets[_key(t)] = sets
    if enums:
        from sqlalchemy.dialects.postgresql.named_types import CreateEnumType

        for enum in enums.values():
            yield {"t": "ddl", "phase": "types", "sql": _ddl(conn, CreateEnumType(enum))}
    fks: list[tuple[str, sa.ForeignKeyConstraint]] = []
    for t in tables:
        for fkc in t.foreign_key_constraints:
            fk = sa.ForeignKeyConstraint(
                [c.name for c in fkc.columns],
                [e.target_fullname for e in fkc.elements],
                name=fkc.name,
                ondelete=fkc.ondelete,
                onupdate=fkc.onupdate,
            )
            fks.append((_key(t), fk))
    if kind == "sqlite":  # no ALTER TABLE ... ADD CONSTRAINT: foreign keys stay inline
        for key, fk in fks:
            copies[key].append_constraint(fk)
    for t in tables:
        yield {"t": "ddl", "phase": "tables", "sql": _ddl(conn, CreateTable(copies[_key(t)]))}
    for t in tables:
        table_copy = copies[_key(t)]
        for ix in t.indexes:
            names = [c.name for c in ix.columns]
            if (
                not names
                or len(names) != len(ix.expressions)
                or frozenset(names) in pk_sets[_key(t)]
            ):
                if not names or len(names) != len(ix.expressions):
                    summary.skipped.append(f"expression index {ix.name} on {_key(t)}")
                continue
            new_ix = sa.Index(ix.name, *[table_copy.c[n] for n in names], unique=bool(ix.unique))
            yield {"t": "ddl", "phase": "indexes", "sql": _ddl(conn, CreateIndex(new_ix))}
    if kind != "sqlite":
        for key, fk in fks:
            try:
                copies[key].append_constraint(fk)
                yield {"t": "ddl", "phase": "constraints", "sql": _ddl(conn, AddConstraint(fk))}
            except Exception:  # a key to a table outside the dumped schemas
                summary.skipped.append(
                    f"foreign key {fk.name or ''} on {key} (target not in the backup)"
                )
    inspector = sa.inspect(conn)
    for s in schemas_of(conn, schema):
        shown = s if kind in ("postgresql", "mssql") else None
        for view in inspector.get_view_names(schema=s):
            body = str(inspector.get_view_definition(view, schema=s) or "").strip().rstrip(";")
            if not body:
                continue
            if kind == "postgresql":
                q = conn.dialect.identifier_preparer
                name = f"{q.quote(shown or 'public')}.{q.quote(view)}"
                body = f"CREATE VIEW {name} AS {body}"
            elif kind == "mysql":
                body = _DEFINER.sub("", body, count=1)
            summary.views.append(f"{shown}.{view}" if shown else view)
            yield {"t": "ddl", "phase": "views", "sql": body}
    if kind == "postgresql":
        for key, cols in serials.items():
            table = copies[key]
            for col in cols:
                yield {"t": "sequence", "schema": table.schema, "table": table.name, "column": col}


def dump(conn: Connection, path: Path, *, schema: str | None, max_rows: int) -> DumpSummary:
    """Write ``conn``'s tables to ``path``. Raises BackupTooLargeError past ``max_rows``."""
    kind = conn.dialect.name
    summary = DumpSummary(engine=kind, server_version=server_version(conn))
    tables = _reflect(conn, schema)
    total = 0
    with gzip.open(path, "wt", encoding="utf-8") as out:

        def write(obj: dict[str, Any]) -> None:
            out.write(json.dumps(obj, separators=(",", ":")) + "\n")

        write(
            {
                "format": FORMAT,
                "engine": kind,
                "server_version": summary.server_version,
                "created_at": datetime.now(UTC).isoformat(),
                "schema": schema,
            }
        )
        for line in _lines(conn, tables, summary, schema):
            write(line)
        for t in tables:
            names = [c.name for c in t.columns if c.computed is None]
            identity = [
                c.name for c in t.columns if c.identity is not None or c.autoincrement is True
            ]
            write(
                {
                    "t": "table",
                    "name": _key(t),
                    "schema": t.schema,
                    "table": t.name,
                    "columns": names,
                    "identity": identity,
                }
            )
            count = 0
            result = conn.execution_options(stream_results=True, max_row_buffer=BATCH).execute(
                sa.select(*[t.c[n] for n in names])
            )
            for part in result.partitions(BATCH):
                rows = [[encode_value(v) for v in r] for r in part]
                count += len(rows)
                total += len(rows)
                if total > max_rows:
                    raise BackupTooLargeError(max_rows)
                write({"t": "rows", "table": _key(t), "rows": rows})
            summary.tables[_key(t)] = count
        write({"t": "end", "tables": summary.tables, "total_rows": total, "views": summary.views})
    return summary


def read_lines(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Integrity:
    ok: bool
    detail: str
    tables: dict[str, int] = field(default_factory=dict)


def verify(path: Path) -> Integrity:
    """The dump reads back whole: a known header, every line parses, every table's rows add
    up to the counts its closing line records."""
    counted: dict[str, int] = {}
    header: dict[str, Any] | None = None
    end: dict[str, Any] | None = None
    try:
        for i, line in enumerate(read_lines(path)):
            if i == 0:
                header = line
                continue
            if line.get("t") == "rows":
                counted[line["table"]] = counted.get(line["table"], 0) + len(line["rows"])
            elif line.get("t") == "table":
                counted.setdefault(line["name"], 0)
            elif line.get("t") == "end":
                end = line
    except (OSError, EOFError, ValueError) as exc:
        return Integrity(False, f"the file does not read back: {exc}")
    if header is None or header.get("format") != FORMAT:
        return Integrity(False, "the file is not a DBPilot dump")
    if end is None:
        return Integrity(False, "the dump is incomplete (no closing record)")
    if end.get("tables") != counted:
        return Integrity(False, "row counts do not match the closing record", counted)
    return Integrity(
        True, f"{len(counted)} tables, {sum(counted.values()):,} rows read back", counted
    )


@dataclass
class RestoreSummary:
    tables: dict[str, int] = field(default_factory=dict)
    expected: dict[str, int] = field(default_factory=dict)
    statements: int = 0

    @property
    def mismatches(self) -> dict[str, tuple[int, int]]:
        return {
            k: (self.expected.get(k, 0), self.tables.get(k, 0))
            for k in set(self.expected) | set(self.tables)
            if self.expected.get(k, 0) != self.tables.get(k, 0)
        }


def restore(
    conn: Connection, path: Path, stopped: Callable[[], bool] = never_stopped
) -> RestoreSummary:
    """Replay a dump on ``conn`` (write mode) and count rows against it. The caller commits
    (``gateway.write``); ``stopped`` is asked before every statement and batch."""
    kind = conn.dialect.name
    lines = read_lines(path)
    header = next(lines)
    if header.get("format") != FORMAT or header.get("engine") != kind:
        raise ValueError(f"this backup is a {header.get('engine')} dump; the target is {kind}")
    summary = RestoreSummary()
    deferred: list[str] = []
    sequences: list[dict[str, Any]] = []
    located: dict[str, tuple[str | None, str]] = {}
    current: sa.Table | None = None
    names: list[str] = []
    identity_on = False

    def check() -> None:
        if stopped():
            raise WriteStoppedError("the restore ran past its time limit")

    def run(sql: str) -> None:
        check()
        if kind == "postgresql":
            pg_execute(conn, sql)  # extended protocol: one statement, never a second
        else:
            conn.exec_driver_sql(sql)
        summary.statements += 1

    def close_table() -> None:
        nonlocal identity_on
        if current is not None and identity_on:
            run(f"SET IDENTITY_INSERT {_quoted(conn, current)} OFF")
        identity_on = False

    for line in lines:
        t = line.get("t")
        if t == "ddl":
            if line["phase"] in ("schemas", "types", "tables"):
                run(line["sql"])
            else:
                deferred.append(line["sql"])
        elif t == "sequence":
            sequences.append(line)
        elif t == "table":
            close_table()
            current = sa.Table(
                line["table"], sa.MetaData(), schema=line.get("schema"), autoload_with=conn
            )
            located[str(line["name"])] = (line.get("schema"), str(line["table"]))
            names = list(line["columns"])
            if kind == "mssql" and set(line.get("identity") or []) & set(names):
                run(f"SET IDENTITY_INSERT {_quoted(conn, current)} ON")
                identity_on = True
        elif t == "rows" and current is not None:
            rows = [
                dict(zip(names, (decode_value(v) for v in r), strict=True)) for r in line["rows"]
            ]
            if rows:
                check()
                conn.execute(current.insert(), rows)
        elif t == "end":
            summary.expected = dict(line.get("tables") or {})
    close_table()
    pending = deferred
    # Views may depend on each other: retry the ones that fail until no progress is made.
    for _ in range(5):
        failed: list[str] = []
        for sql in pending:
            nested = conn.begin_nested() if kind != "mysql" else None
            try:
                run(sql)
                if nested is not None:
                    nested.commit()
            except WriteStoppedError:
                raise
            except Exception:
                if nested is not None:
                    nested.rollback()
                failed.append(sql)
        if not failed or len(failed) == len(pending):
            pending = failed
            break
        pending = failed
    if pending:
        raise ValueError(f"{len(pending)} statement(s) of the backup could not be replayed")
    for seq in sequences:
        check()
        set_sequence(conn, seq.get("schema"), str(seq["table"]), str(seq["column"]))
        summary.statements += 1
    for name in summary.expected:
        schema, table = located.get(name, (None, name))
        t_obj = sa.table(table, schema=schema)
        summary.tables[name] = int(
            conn.execute(sa.select(sa.func.count()).select_from(t_obj)).scalar() or 0
        )
    return summary
