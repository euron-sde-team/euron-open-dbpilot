"""Schema discovery for the SQL engines: tables and views the login can read outside system
schemas, with columns, types, defaults, primary and foreign keys, indexes and row-count
estimates. The explorer, the AI assistant's context, the ER view, autocomplete and schema
comparison all read the same result (AC6)."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.engine.reflection import ObjectKind

SYSTEM_SCHEMAS = {
    "postgresql": {"pg_catalog", "information_schema", "pg_toast"},
    "mysql": {"mysql", "information_schema", "performance_schema", "sys"},
    "mssql": {
        "sys",
        "INFORMATION_SCHEMA",
        "guest",
        "db_owner",
        "db_accessadmin",
        "db_securityadmin",
        "db_ddladmin",
        "db_backupoperator",
        "db_datareader",
        "db_datawriter",
        "db_denydatareader",
        "db_denydatawriter",
    },
}
# Sampled distinct values are kept only for columns with at most this many.
SAMPLE_DISTINCT = 12
SAMPLE_MAX_TABLE_ROWS = 2_000_000
_TEXT_TYPES = ("char", "text", "string", "enum", "varchar", "citext")


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    default: str | None = None
    autoincrement: bool = False
    sample_values: list[str] = field(default_factory=list)


@dataclass
class ForeignKeyInfo:
    name: str | None
    columns: list[str]
    ref_table: str  # schema qualified when the engine has schemas
    ref_columns: list[str]


@dataclass
class IndexInfo:
    name: str
    columns: list[str]
    unique: bool


@dataclass
class TableInfo:
    schema: str | None
    name: str
    kind: str  # table or view
    columns: list[ColumnInfo] = field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    indexes: list[IndexInfo] = field(default_factory=list)
    row_count: int | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


def qualifies(kind: str) -> bool:
    """Engines whose table names are shown schema qualified."""
    return kind in ("postgresql", "mssql")


def schemas_of(conn: Connection, schema: str | None) -> list[str | None]:
    kind = conn.dialect.name
    if kind == "sqlite":
        return [None]
    if schema:
        return [schema]
    if kind == "mysql":
        current = conn.exec_driver_sql("SELECT DATABASE()").scalar()
        return [str(current)] if current else []
    inspector = sa.inspect(conn)
    hidden = SYSTEM_SCHEMAS.get(kind, set())
    return [s for s in inspector.get_schema_names() if s not in hidden and not s.startswith("pg_")]


def _row_counts(
    conn: Connection, kind: str, schema: str | None, names: list[str]
) -> dict[str, int]:
    """Cheap row counts: catalog estimates on servers, exact counts on SQLite files."""
    out: dict[str, int] = {}
    quote = conn.dialect.identifier_preparer.quote
    if kind == "postgresql":
        rows = conn.execute(
            sa.text(
                "SELECT c.relname, c.reltuples::bigint FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'm')"
            ),
            {"schema": schema},
        ).all()
        out = {str(r[0]): max(int(r[1]), 0) for r in rows}
        # A table never analyzed reports -1 (0 above): count it if it is small.
        for name in names:
            if out.get(name, 0) == 0:
                n = conn.exec_driver_sql(
                    f"SELECT count(*) FROM (SELECT 1 FROM {quote(schema or '')}.{quote(name)} "
                    "LIMIT 100001) s"
                ).scalar()
                out[name] = int(n or 0)
    elif kind == "mysql":
        rows = conn.execute(
            sa.text(
                "SELECT table_name, table_rows FROM information_schema.tables "
                "WHERE table_schema = :schema"
            ),
            {"schema": schema},
        ).all()
        out = {str(r[0]): int(r[1] or 0) for r in rows}
        for name in names:
            if out.get(name, 0) == 0:
                n = conn.exec_driver_sql(
                    f"SELECT count(*) FROM (SELECT 1 FROM {quote(name)} LIMIT 100001) s"
                ).scalar()
                out[name] = int(n or 0)
    elif kind == "mssql":
        rows = conn.execute(
            sa.text(
                "SELECT t.name, SUM(p.rows) FROM sys.tables t "
                "JOIN sys.schemas s ON s.schema_id = t.schema_id "
                "JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1) "
                "WHERE s.name = :schema GROUP BY t.name"
            ),
            {"schema": schema},
        ).all()
        out = {str(r[0]): int(r[1] or 0) for r in rows}
    else:
        for name in names[:500]:
            n = conn.exec_driver_sql(f"SELECT count(*) FROM {quote(name)}").scalar()
            out[name] = int(n or 0)
    return out


def _is_text(type_name: str) -> bool:
    lower = type_name.lower()
    return any(t in lower for t in _TEXT_TYPES)


def _sample(conn: Connection, table: TableInfo, column: ColumnInfo) -> list[str]:
    quote = conn.dialect.identifier_preparer.quote
    target = f"{quote(table.schema)}.{quote(table.name)}" if table.schema else quote(table.name)
    col = quote(column.name)
    if conn.dialect.name == "mssql":
        sql = (
            f"SELECT TOP {SAMPLE_DISTINCT + 1} {col} FROM {target} WHERE {col} IS NOT NULL "
            f"GROUP BY {col} ORDER BY count(*) DESC"
        )
    else:
        sql = (
            f"SELECT {col} FROM {target} WHERE {col} IS NOT NULL GROUP BY {col} "
            f"ORDER BY count(*) DESC LIMIT {SAMPLE_DISTINCT + 1}"
        )
    rows = conn.exec_driver_sql(sql).all()
    if len(rows) > SAMPLE_DISTINCT:
        return []
    return [str(r[0])[:60] for r in rows]


def _type_text(conn: Connection, raw_type: object) -> str:
    """The column type as the engine's own DDL spells it (used by schema comparison)."""
    # An exotic reflected type that does not compile keeps its plain name.
    with contextlib.suppress(Exception):
        if isinstance(raw_type, sa.types.TypeEngine):
            return str(raw_type.compile(dialect=conn.dialect))[:200]
    return str(raw_type)[:200]


def discover(
    conn: Connection,
    schema: str | None = None,
    *,
    max_tables: int = 2000,
    sample_values: bool = False,
) -> list[TableInfo]:
    kind = conn.dialect.name
    inspector = sa.inspect(conn)
    out: list[TableInfo] = []
    for s in schemas_of(conn, schema):
        table_names = inspector.get_table_names(schema=s)
        view_names = inspector.get_view_names(schema=s)
        names = [*table_names, *view_names][: max(max_tables - len(out), 0)]
        if not names:
            continue
        anything = ObjectKind.ANY
        columns = inspector.get_multi_columns(schema=s, filter_names=names, kind=anything)
        pks = inspector.get_multi_pk_constraint(schema=s, filter_names=names, kind=anything)
        fks = inspector.get_multi_foreign_keys(schema=s, filter_names=names, kind=anything)
        indexes = inspector.get_multi_indexes(schema=s, filter_names=names, kind=anything)
        counts = _row_counts(conn, kind, s, [n for n in names if n in table_names])
        # MySQL's "schema" is the connection's own database: names stay unqualified.
        shown_schema = s if qualifies(kind) else None
        for name in names:
            key = (s, name)
            pk = pks.get(key)
            pk_cols = set(pk["constrained_columns"] or []) if pk else set()
            info = TableInfo(
                schema=shown_schema,
                name=name,
                kind="view" if name in view_names else "table",
                row_count=counts.get(name) if name in table_names else None,
            )
            for c in columns.get(key, []):
                default = c.get("default")
                info.columns.append(
                    ColumnInfo(
                        name=str(c["name"]),
                        type=_type_text(conn, c["type"]),
                        nullable=bool(c.get("nullable", True)),
                        primary_key=str(c["name"]) in pk_cols,
                        default=str(default)[:300] if default is not None else None,
                        autoincrement=c.get("autoincrement") is True
                        or bool(c.get("identity"))
                        or "nextval(" in str(default or ""),
                    )
                )
            for fk in fks.get(key, []):
                ref_schema = fk.get("referred_schema") or s
                ref = fk["referred_table"]
                info.foreign_keys.append(
                    ForeignKeyInfo(
                        name=str(fk["name"]) if fk.get("name") else None,
                        columns=list(fk["constrained_columns"]),
                        ref_table=f"{ref_schema}.{ref}" if qualifies(kind) else str(ref),
                        ref_columns=list(fk["referred_columns"]),
                    )
                )
            for ix in indexes.get(key, []):
                cols = [str(c) for c in ix.get("column_names") or [] if c]
                if cols:
                    info.indexes.append(
                        IndexInfo(
                            name=str(ix.get("name") or ""),
                            columns=cols,
                            unique=bool(ix.get("unique")),
                        )
                    )
            if (
                sample_values
                and info.kind == "table"
                and (info.row_count or 0) <= SAMPLE_MAX_TABLE_ROWS
            ):
                for col in info.columns:
                    if _is_text(col.type) and not col.primary_key:
                        col.sample_values = _sample(conn, info, col)
            out.append(info)
        if len(out) >= max_tables:
            break
    return sorted(out, key=lambda t: ((t.schema or ""), t.name))
