"""AC6: an AI-generated statement may only name tables and columns the live schema
discovery returned.

Every table reference must be a discovered table or view (or a CTE the statement defines);
every column reference must belong to a table it reads, to a CTE or subquery it defines
(referenced from outside that definition), or be an output alias it names (referenced from
ORDER BY, GROUP BY, HAVING or WHERE, never from inside the expression the alias names). A
statement naming anything else is refused as fabricated, whatever else it does, and is
never executable. So ``SELECT salary AS salary FROM users`` does not vouch for itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlglot import exp

from dbpilot.schema.schemas import SchemaObject

_DERIVED = object()
# Clauses that may name a SELECT's output alias (WHERE only on SQLite; elsewhere the
# database itself refuses it, which is no fabrication).
_ALIAS_CLAUSES = (exp.Order, exp.Group, exp.Having, exp.Where)


def _ancestors(node: exp.Expr) -> Iterator[exp.Expr]:
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


@dataclass
class IdentifierReport:
    unknown_tables: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unknown_tables and not self.unknown_columns

    def message(self) -> str:
        parts = []
        if self.unknown_tables:
            parts.append("tables " + ", ".join(self.unknown_tables))
        if self.unknown_columns:
            parts.append("columns " + ", ".join(self.unknown_columns))
        return (
            "The statement names "
            + " and ".join(parts)
            + " that the discovered schema does not contain"
        )


def _projection_names(node: exp.Expression | None) -> set[str] | None:
    """Output column names of a SELECT (None when it selects ``*``, so unknown)."""
    if not isinstance(node, exp.Query):
        return None
    names: set[str] = set()
    for proj in node.selects:
        if isinstance(proj, exp.Star) or (
            isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
        ):
            return None
        names.add(str(proj.alias_or_name).lower())
    return names


def check_identifiers(tree: exp.Expression, objects: list[SchemaObject]) -> IdentifierReport:
    report = IdentifierReport()
    by_name: dict[str, SchemaObject] = {}
    for o in objects:
        by_name.setdefault(o.name.lower(), o)
        by_name[o.qualified.lower()] = o
    derived_columns: dict[str, set[str] | None] = {}
    # Each CTE or aliased subquery with the column names it defines. A recursive CTE reads
    # itself, so its own names are in scope inside its definition too (``None``).
    definitions: list[tuple[exp.Expression | None, set[str]]] = []
    for cte in tree.find_all(exp.CTE):
        alias = str(cte.alias_or_name).lower()
        columns = cte.args.get("alias")
        listed = (
            {str(c.name).lower() for c in columns.columns}
            if isinstance(columns, exp.TableAlias) and columns.columns
            else None
        )
        derived_columns[alias] = listed or _projection_names(cte.this)
        if derived_columns[alias]:
            reads_itself = any(
                not t.db and str(t.name).lower() == alias for t in cte.this.find_all(exp.Table)
            )
            definitions.append((None if reads_itself else cte, derived_columns[alias] or set()))
    for sq in tree.find_all(exp.Subquery):
        if sq.alias:
            names = _projection_names(sq.this)
            derived_columns[str(sq.alias).lower()] = names
            if names:
                definitions.append((sq, names))

    scopes: dict[str, object] = {}
    real: list[SchemaObject] = []
    for t in tree.find_all(exp.Table):
        name = str(t.name).lower()
        if not name:
            continue
        db = str(t.db).lower() if t.db else ""
        if not db and name in derived_columns:
            scopes[str(t.alias_or_name).lower()] = _DERIVED
            scopes.setdefault(name, _DERIVED)
            continue
        obj = by_name.get(f"{db}.{name}" if db else name) or (None if db else by_name.get(name))
        if obj is None:
            report.unknown_tables.append(str(t.sql()))
            continue
        real.append(obj)
        scopes[str(t.alias_or_name).lower()] = obj
        scopes.setdefault(name, obj)
    for alias in derived_columns:
        scopes.setdefault(alias, _DERIVED)

    real_columns = {c.name.lower() for o in real for c in o.columns}
    output_aliases = {str(a.alias).lower() for a in tree.find_all(exp.Alias) if a.alias}

    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        cname = str(col.name).lower()
        if not cname:
            continue
        qualifier = str(col.table).lower() if col.table else ""
        if qualifier:
            scope = scopes.get(qualifier)
            if scope is None:
                report.unknown_columns.append(str(col.sql()))
            elif scope is _DERIVED:
                known = derived_columns.get(qualifier)
                if known is not None and cname not in known:
                    report.unknown_columns.append(str(col.sql()))
            elif isinstance(scope, SchemaObject) and cname not in {
                c.name.lower() for c in scope.columns
            }:
                report.unknown_columns.append(str(col.sql()))
            continue
        # A SELECT * inside a CTE or subquery passes the columns of the real tables it reads,
        # which are already in ``real_columns``.
        if cname in real_columns:
            continue
        above = list(_ancestors(col))
        inside = {id(n) for n in above}
        own = {str(n.alias).lower() for n in above if isinstance(n, exp.Alias) and n.alias}
        if (
            cname in output_aliases
            and cname not in own
            and any(isinstance(n, _ALIAS_CLAUSES) for n in above)
        ):
            continue
        if any(cname in names for d, names in definitions if d is None or id(d) not in inside):
            continue
        report.unknown_columns.append(str(col.sql()))
    report.unknown_tables = sorted(set(report.unknown_tables))
    report.unknown_columns = sorted(set(report.unknown_columns))
    return report
