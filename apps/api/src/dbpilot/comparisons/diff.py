"""The difference between two schema snapshots of SQL databases: tables added and removed,
and per table the columns, indexes and foreign keys that differ."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dbpilot.schema.schemas import SchemaColumn, SchemaForeignKey, SchemaIndex, SchemaObject


def _norm_type(t: str) -> str:
    return " ".join(t.lower().replace(" ,", ",").replace(", ", ",").split())


@dataclass
class ColumnChange:
    name: str
    before: SchemaColumn
    after: SchemaColumn

    @property
    def type_changed(self) -> bool:
        return _norm_type(self.before.type) != _norm_type(self.after.type)

    @property
    def null_changed(self) -> bool:
        return self.before.nullable != self.after.nullable


@dataclass
class TableDiff:
    table: SchemaObject  # the target ("to") definition
    added_columns: list[SchemaColumn] = field(default_factory=list)
    removed_columns: list[SchemaColumn] = field(default_factory=list)
    changed_columns: list[ColumnChange] = field(default_factory=list)
    added_indexes: list[SchemaIndex] = field(default_factory=list)
    removed_indexes: list[SchemaIndex] = field(default_factory=list)
    added_foreign_keys: list[SchemaForeignKey] = field(default_factory=list)
    removed_foreign_keys: list[SchemaForeignKey] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.added_columns,
                self.removed_columns,
                self.changed_columns,
                self.added_indexes,
                self.removed_indexes,
                self.added_foreign_keys,
                self.removed_foreign_keys,
            )
        )


@dataclass
class SchemaDiff:
    added_tables: list[SchemaObject] = field(default_factory=list)
    removed_tables: list[SchemaObject] = field(default_factory=list)
    changed_tables: list[TableDiff] = field(default_factory=list)
    added_views: list[str] = field(default_factory=list)
    removed_views: list[str] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not any(
            (
                self.added_tables,
                self.removed_tables,
                self.changed_tables,
                self.added_views,
                self.removed_views,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "identical": self.identical,
            "added_tables": [t.qualified for t in self.added_tables],
            "removed_tables": [t.qualified for t in self.removed_tables],
            "added_views": self.added_views,
            "removed_views": self.removed_views,
            "changed_tables": [
                {
                    "table": d.table.qualified,
                    "added_columns": [c.name for c in d.added_columns],
                    "removed_columns": [c.name for c in d.removed_columns],
                    "changed_columns": [
                        {
                            "name": c.name,
                            "from": {"type": c.before.type, "nullable": c.before.nullable},
                            "to": {"type": c.after.type, "nullable": c.after.nullable},
                        }
                        for c in d.changed_columns
                    ],
                    "added_indexes": [i.name for i in d.added_indexes],
                    "removed_indexes": [i.name for i in d.removed_indexes],
                    "added_foreign_keys": [
                        f.name or ",".join(f.columns) for f in d.added_foreign_keys
                    ],
                    "removed_foreign_keys": [
                        f.name or ",".join(f.columns) for f in d.removed_foreign_keys
                    ],
                }
                for d in self.changed_tables
            ],
        }


def _index_key(ix: SchemaIndex) -> tuple[tuple[str, ...], bool]:
    return tuple(c.lower() for c in ix.columns), ix.unique


def _fk_key(fk: SchemaForeignKey) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    return (
        tuple(c.lower() for c in fk.columns),
        fk.ref_table.lower(),
        tuple(c.lower() for c in fk.ref_columns),
    )


def _pk_index(t: SchemaObject, ix: SchemaIndex) -> bool:
    pk = {c.name.lower() for c in t.columns if c.primary_key}
    return bool(pk) and {c.lower() for c in ix.columns} == pk


def compare(before: list[SchemaObject], after: list[SchemaObject]) -> SchemaDiff:
    """What turns ``before`` into ``after``."""
    diff = SchemaDiff()
    old = {o.qualified.lower(): o for o in before if o.kind == "table"}
    new = {o.qualified.lower(): o for o in after if o.kind == "table"}
    old_views = {o.qualified for o in before if o.kind == "view"}
    new_views = {o.qualified for o in after if o.kind == "view"}
    diff.added_views = sorted(new_views - old_views)
    diff.removed_views = sorted(old_views - new_views)
    diff.added_tables = [new[k] for k in sorted(new.keys() - old.keys())]
    diff.removed_tables = [old[k] for k in sorted(old.keys() - new.keys())]
    for key in sorted(old.keys() & new.keys()):
        a, b = old[key], new[key]
        d = TableDiff(table=b)
        a_cols = {c.name.lower(): c for c in a.columns}
        b_cols = {c.name.lower(): c for c in b.columns}
        d.added_columns = [b_cols[k] for k in b_cols if k not in a_cols]
        d.removed_columns = [a_cols[k] for k in a_cols if k not in b_cols]
        for k in a_cols.keys() & b_cols.keys():
            change = ColumnChange(b_cols[k].name, a_cols[k], b_cols[k])
            if change.type_changed or change.null_changed:
                d.changed_columns.append(change)
        a_ix = {_index_key(i): i for i in a.indexes if not _pk_index(a, i)}
        b_ix = {_index_key(i): i for i in b.indexes if not _pk_index(b, i)}
        d.added_indexes = [b_ix[k] for k in b_ix if k not in a_ix]
        d.removed_indexes = [a_ix[k] for k in a_ix if k not in b_ix]
        a_fk = {_fk_key(f): f for f in a.foreign_keys}
        b_fk = {_fk_key(f): f for f in b.foreign_keys}
        d.added_foreign_keys = [b_fk[k] for k in b_fk if k not in a_fk]
        d.removed_foreign_keys = [a_fk[k] for k in a_fk if k not in b_fk]
        if not d.empty:
            diff.changed_tables.append(d)
    return diff
