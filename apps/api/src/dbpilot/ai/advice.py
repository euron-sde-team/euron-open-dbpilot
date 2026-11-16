"""Index suggestions and error diagnosis, grounded in the real schema.

Index suggestions come from two facts DBPilot can check rather than guess: a foreign key
column with no index whose leading column it is (joins and deletes on the referenced table
then scan this one), and a column this connection's own recent queries filter on that no
index leads with. Each suggestion carries ready DDL a person may review and run through the
confirmed-write path; DBPilot never creates an index itself.

Diagnosis reads the database's own error message, names the kind of problem, and for an
unknown table or column suggests the closest names the discovered schema really has.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field

from sqlglot import exp

from dbpilot.queries import guard
from dbpilot.schema.schemas import SchemaObject

SQL_ENGINES = ("postgresql", "mysql", "mssql", "sqlite")


@dataclass
class IndexSuggestion:
    table: str
    columns: list[str]
    reason: str
    ddl: str
    source: str  # foreign_key, query_history


def _quote(name: str, dialect: str) -> str:
    return str(exp.to_identifier(name, quoted=True).sql(dialect=guard.SQLGLOT_DIALECT[dialect]))


def _qualified(o: SchemaObject, dialect: str) -> str:
    parts = [o.schema_name, o.name] if o.schema_name else [o.name]
    return ".".join(_quote(p, dialect) for p in parts if p)


def ddl_for(o: SchemaObject, columns: list[str], dialect: str) -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", f"ix_{o.name}_{'_'.join(columns)}".lower())[:60]
    cols = ", ".join(_quote(c, dialect) for c in columns)
    return f"CREATE INDEX {_quote(name, dialect)} ON {_qualified(o, dialect)} ({cols})"


def _leading(o: SchemaObject) -> set[str]:
    leads = {ix.columns[0].lower() for ix in o.indexes if ix.columns}
    pk = [c.name.lower() for c in o.columns if c.primary_key]
    if pk:
        leads.add(pk[0])
    return leads


def filtered_columns(statements: list[str], dialect: str) -> Counter[tuple[str, str]]:
    """(table, column) pairs that WHERE clauses of ``statements`` compare, counted."""
    counts: Counter[tuple[str, str]] = Counter()
    for sql in statements:
        try:
            trees = guard.parse(sql, dialect)
        except guard.UnsafeSqlError:
            continue
        for tree in trees:
            aliases = {
                str(t.alias_or_name).lower(): str(t.name).lower() for t in tree.find_all(exp.Table)
            }
            only = next(iter(aliases.values())) if len(set(aliases.values())) == 1 else None
            for where in tree.find_all(exp.Where):
                for col in where.find_all(exp.Column):
                    table = aliases.get(str(col.table).lower()) if col.table else only
                    if table:
                        counts[(table, str(col.name).lower())] += 1
    return counts


def index_suggestions(
    objects: list[SchemaObject], dialect: str, history: list[str], limit: int = 20
) -> list[IndexSuggestion]:
    out: list[IndexSuggestion] = []
    tables = [o for o in objects if o.kind == "table"]
    for o in tables:
        leading = _leading(o)
        for fk in o.foreign_keys:
            if fk.columns and fk.columns[0].lower() not in leading:
                out.append(
                    IndexSuggestion(
                        table=o.qualified,
                        columns=list(fk.columns),
                        reason=(
                            f"The foreign key ({', '.join(fk.columns)}) to {fk.ref_table} has no "
                            "index: joins on it, and deletes or updates of the referenced rows, "
                            f"read all of {o.name}"
                        ),
                        ddl=ddl_for(o, list(fk.columns), dialect),
                        source="foreign_key",
                    )
                )
                leading.add(fk.columns[0].lower())
    by_name = {o.name.lower(): o for o in tables}
    for (table, column), count in filtered_columns(history, dialect).most_common():
        found = by_name.get(table)
        if found is None or column in _leading(found) or (found.row_count or 0) < 1000:
            continue
        o = found
        if not any(c.name.lower() == column for c in o.columns):
            continue
        if any(s.table == o.qualified and s.columns[0].lower() == column for s in out):
            continue
        out.append(
            IndexSuggestion(
                table=o.qualified,
                columns=[column],
                reason=(
                    f"Recent queries on this connection filtered {o.name}.{column} {count} "
                    f"time(s), and no index starts with it ({o.row_count:,} rows)"
                ),
                ddl=ddl_for(o, [column], dialect),
                source="query_history",
            )
        )
    return out[:limit]


@dataclass
class Diagnosis:
    category: str
    explanation: str
    suggestion: str
    similar: list[str] = field(default_factory=list)


_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "unknown_column",
        re.compile(
            r"column \"?([\w.]+)\"? does not exist|unknown column '([^']+)'"
            r"|invalid column name '([^']+)'|no such column: ([\w.]+)",
            re.I,
        ),
    ),
    (
        "unknown_table",
        re.compile(
            r"relation \"?([\w.]+)\"? does not exist|table '([^']+)' doesn't exist"
            r"|invalid object name '([^']+)'|no such table: ([\w.]+)",
            re.I,
        ),
    ),
    (
        "read_only",
        re.compile(
            r"read-only transaction|read only transaction|readonly database|not authorized", re.I
        ),
    ),
    ("permission", re.compile(r"permission denied|access denied|denied", re.I)),
    (
        "timeout",
        re.compile(
            r"statement timeout|canceling statement|maximum statement execution time|timed out",
            re.I,
        ),
    ),
    (
        "group_by",
        re.compile(
            r"must appear in the group by|isn't in group by|not contained in either an aggregate",
            re.I,
        ),
    ),
    ("ambiguous", re.compile(r"ambiguous", re.I)),
    (
        "type_mismatch",
        re.compile(
            r"operator does not exist|invalid input syntax|conversion failed|data type mismatch",
            re.I,
        ),
    ),
    ("syntax", re.compile(r"syntax error|error in your sql syntax|incorrect syntax near", re.I)),
]

_EXPLAIN = {
    "unknown_column": (
        "The statement names a column this database does not have.",
        "Use one of the real column names below, or check which table the column belongs to.",
    ),
    "unknown_table": (
        "The statement names a table or view this database does not have (or the login "
        "cannot see).",
        "Use one of the real table names below, schema qualified where the database uses schemas.",
    ),
    "read_only": (
        "The statement tried to change data on a read-only session.",
        "Read queries cannot write. To change data, mark the statement as a write: it is "
        "described and confirmed separately, if you hold write access.",
    ),
    "permission": (
        "The database login DBPilot uses may not read what the statement asks for.",
        "Ask the database administrator to grant the login SELECT on it, or query something "
        "it can read.",
    ),
    "timeout": (
        "The database stopped the statement at the connection's time limit.",
        "Narrow it with a filter or a smaller time range, add an index on the filtered "
        "column, or raise the connection's timeout.",
    ),
    "group_by": (
        "A column in the SELECT list is neither grouped nor aggregated.",
        "Add the column to GROUP BY, or wrap it in an aggregate such as MAX() or COUNT().",
    ),
    "ambiguous": (
        "A column name exists in more than one joined table.",
        "Prefix it with the table name or alias, for example o.id.",
    ),
    "type_mismatch": (
        "Two values of different types are compared or converted.",
        "Cast one side explicitly, or compare like with like (a date with a date, a number "
        "with a number).",
    ),
    "syntax": (
        "The database could not parse the statement.",
        "Check commas, parentheses and quotes near the position the error names, and the "
        "dialect's syntax.",
    ),
}


def diagnose(sql: str, error: str, objects: list[SchemaObject]) -> Diagnosis:
    for category, pattern in _PATTERNS:
        m = pattern.search(error)
        if not m:
            continue
        explanation, suggestion = _EXPLAIN[category]
        similar: list[str] = []
        name = next((g for g in m.groups() if g), "") if m.groups() else ""
        if category == "unknown_column" and name:
            candidates = sorted({c.name for o in objects for c in o.columns})
            similar = difflib.get_close_matches(name.split(".")[-1], candidates, n=5, cutoff=0.5)
        if category == "unknown_table" and name:
            candidates = sorted({o.qualified for o in objects} | {o.name for o in objects})
            similar = difflib.get_close_matches(name.split(".")[-1], candidates, n=5, cutoff=0.4)
        return Diagnosis(category, explanation, suggestion, similar)
    return Diagnosis(
        "other",
        "The database refused the statement: " + error.strip().splitlines()[0][:300]
        if error.strip()
        else "The database refused the statement.",
        "Read the database's message above; the explorer shows the real tables and columns.",
    )
