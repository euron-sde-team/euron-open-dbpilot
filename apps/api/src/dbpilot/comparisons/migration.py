"""A migration script from a schema diff, in the dialect of the database it would run on.

Statements come in a safe order: new tables (without foreign keys), new columns, changed
columns, new indexes, new foreign keys; then everything destructive (dropped foreign keys,
indexes, columns, tables), each flagged. DBPilot never runs this script itself (AC8): a
person reviews it and runs it through the confirmed-write path. What an engine cannot do
in place (SQLite changing a column, say) is reported as a note instead of a statement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlglot import exp

from dbpilot.comparisons.diff import SchemaDiff
from dbpilot.queries import guard
from dbpilot.schema.schemas import SchemaColumn, SchemaForeignKey, SchemaIndex, SchemaObject

_INTEGER = re.compile(r"^(big|small)?int(eger)?$|^bigint$|^integer$", re.I)


@dataclass
class Script:
    statements: list[tuple[str, bool]] = field(default_factory=list)  # (sql, destructive)
    notes: list[str] = field(default_factory=list)

    @property
    def sql(self) -> str:
        return ";\n".join(s for s, _ in self.statements) + (";" if self.statements else "")

    @property
    def destructive(self) -> bool:
        return any(d for _, d in self.statements)


def q(name: str, dialect: str) -> str:
    return str(exp.to_identifier(name, quoted=True).sql(dialect=guard.SQLGLOT_DIALECT[dialect]))


def qualified(t: SchemaObject | str, dialect: str) -> str:
    text = t.qualified if isinstance(t, SchemaObject) else t
    return ".".join(q(p, dialect) for p in text.split(".") if p)


def _column_def(c: SchemaColumn, dialect: str, *, in_create: bool) -> str:
    type_ = c.type
    default = c.default
    tail = ""
    if c.autoincrement:
        if dialect == "postgresql" and _INTEGER.match(type_.strip()):
            type_ = "BIGSERIAL" if type_.lower().startswith("big") else "SERIAL"
            default = None
        elif dialect == "postgresql" and default and "nextval(" in default:
            default = None
        elif dialect == "mysql":
            tail = " AUTO_INCREMENT"
            default = None
        elif dialect == "mssql":
            tail = " IDENTITY(1,1)"
            default = None
    parts = [q(c.name, dialect), type_]
    if not c.nullable or (in_create and c.primary_key):
        parts.append("NOT NULL")
    if default is not None and not (dialect == "postgresql" and "nextval(" in default):
        parts.append(f"DEFAULT {default}")
    return " ".join(parts) + tail


def _create_table(t: SchemaObject, dialect: str) -> str:
    cols = [_column_def(c, dialect, in_create=True) for c in t.columns]
    pk = [q(c.name, dialect) for c in t.columns if c.primary_key]
    if pk:
        cols.append(f"PRIMARY KEY ({', '.join(pk)})")
    return f"CREATE TABLE {qualified(t, dialect)} (\n  " + ",\n  ".join(cols) + "\n)"


def _create_index(t: SchemaObject, ix: SchemaIndex, dialect: str) -> str:
    unique = "UNIQUE " if ix.unique else ""
    cols = ", ".join(q(c, dialect) for c in ix.columns)
    return f"CREATE {unique}INDEX {q(ix.name, dialect)} ON {qualified(t, dialect)} ({cols})"


def _drop_index(t: SchemaObject, ix: SchemaIndex, dialect: str) -> str:
    if dialect == "postgresql":
        schema = f"{q(t.schema_name, dialect)}." if t.schema_name else ""
        return f"DROP INDEX {schema}{q(ix.name, dialect)}"
    if dialect == "sqlite":
        return f"DROP INDEX {q(ix.name, dialect)}"
    return f"DROP INDEX {q(ix.name, dialect)} ON {qualified(t, dialect)}"


def _fk_name(t: SchemaObject, fk: SchemaForeignKey) -> str:
    return fk.name or f"fk_{t.name}_{'_'.join(fk.columns)}"[:60]


def _add_fk(t: SchemaObject, fk: SchemaForeignKey, dialect: str) -> str:
    cols = ", ".join(q(c, dialect) for c in fk.columns)
    refs = ", ".join(q(c, dialect) for c in fk.ref_columns)
    return (
        f"ALTER TABLE {qualified(t, dialect)} ADD CONSTRAINT {q(_fk_name(t, fk), dialect)} "
        f"FOREIGN KEY ({cols}) REFERENCES {qualified(fk.ref_table, dialect)} ({refs})"
    )


def _drop_fk(t: SchemaObject, fk: SchemaForeignKey, dialect: str) -> str:
    verb = "DROP FOREIGN KEY" if dialect == "mysql" else "DROP CONSTRAINT"
    return f"ALTER TABLE {qualified(t, dialect)} {verb} {q(_fk_name(t, fk), dialect)}"


def generate(diff: SchemaDiff, dialect: str) -> Script:
    s = Script()
    add_kw = "ADD" if dialect == "mssql" else "ADD COLUMN"
    for t in diff.added_tables:
        s.statements.append((_create_table(t, dialect), False))
    for d in diff.changed_tables:
        for c in d.added_columns:
            col = _column_def(c, dialect, in_create=False)
            s.statements.append(
                (f"ALTER TABLE {qualified(d.table, dialect)} {add_kw} {col}", False)
            )
            if not c.nullable and c.default is None:
                s.notes.append(
                    f"{d.table.qualified}.{c.name} is NOT NULL without a default: adding it fails "
                    "while the table has rows"
                )
    for d in diff.changed_tables:
        target = qualified(d.table, dialect)
        for ch in d.changed_columns:
            col = q(ch.name, dialect)
            after = ch.after
            if dialect == "sqlite":
                s.notes.append(
                    f"SQLite cannot change {d.table.qualified}.{ch.name} in place "
                    f"({ch.before.type} to {after.type}); rebuild the table to apply it"
                )
            elif dialect == "postgresql":
                if ch.type_changed:
                    s.statements.append(
                        (f"ALTER TABLE {target} ALTER COLUMN {col} TYPE {after.type}", False)
                    )
                if ch.null_changed:
                    verb = "DROP NOT NULL" if after.nullable else "SET NOT NULL"
                    s.statements.append((f"ALTER TABLE {target} ALTER COLUMN {col} {verb}", False))
            else:
                null = "NULL" if after.nullable else "NOT NULL"
                verb = "MODIFY COLUMN" if dialect == "mysql" else "ALTER COLUMN"
                s.statements.append(
                    (f"ALTER TABLE {target} {verb} {col} {after.type} {null}", False)
                )
    for t in diff.added_tables:
        for ix in t.indexes:
            pk = {c.name.lower() for c in t.columns if c.primary_key}
            if {c.lower() for c in ix.columns} != pk:
                s.statements.append((_create_index(t, ix, dialect), False))
    for d in diff.changed_tables:
        for ix in d.added_indexes:
            s.statements.append((_create_index(d.table, ix, dialect), False))
    fks = [(t, fk) for t in diff.added_tables for fk in t.foreign_keys]
    fks += [(d.table, fk) for d in diff.changed_tables for fk in d.added_foreign_keys]
    for t, fk in fks:
        if dialect == "sqlite":
            s.notes.append(
                f"SQLite cannot add the foreign key {_fk_name(t, fk)} to an existing table"
            )
        else:
            s.statements.append((_add_fk(t, fk, dialect), False))
    for d in diff.changed_tables:
        for fk in d.removed_foreign_keys:
            if dialect == "sqlite":
                s.notes.append(
                    f"SQLite cannot drop the foreign key {_fk_name(d.table, fk)} in place"
                )
            else:
                s.statements.append((_drop_fk(d.table, fk, dialect), True))
        for ix in d.removed_indexes:
            s.statements.append((_drop_index(d.table, ix, dialect), True))
        for c in d.removed_columns:
            s.statements.append(
                (
                    f"ALTER TABLE {qualified(d.table, dialect)} DROP COLUMN {q(c.name, dialect)}",
                    True,
                )
            )
    for t in diff.removed_tables:
        s.statements.append((f"DROP TABLE {qualified(t, dialect)}", True))
    if diff.added_views or diff.removed_views:
        s.notes.append(
            "Views differ ("
            + ", ".join(
                [*(f"+{v}" for v in diff.added_views), *(f"-{v}" for v in diff.removed_views)]
            )
            + "); snapshots do not hold view definitions, so they are not scripted"
        )
    return s
