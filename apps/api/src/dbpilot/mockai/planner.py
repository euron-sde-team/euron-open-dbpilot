"""The deterministic mock model's SQL writer: an ``Intent`` becomes SQL in the connection's
dialect, using only tables and columns from the prompt's schema block.

Like a real model it can be talked into writing a destructive statement ("delete the
cancelled orders", "drop the customers table"): that is exactly what the SQL guard exists
to refuse, and what the AC4 tests ask it for.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from dbpilot.ai.prompt import stem, words
from dbpilot.mockai.intent import (
    CannotAnswerError,
    Graph,
    Intent,
    Ref,
    is_key,
    is_numeric,
    parse,
    value_filters,
)
from dbpilot.schema.schemas import SchemaObject


@dataclass
class Plan:
    sql: str | None
    note: str


def quote(name: str, dialect: str) -> str:
    if re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        return name
    if dialect == "mysql":
        return f"`{name}`"
    if dialect == "mssql":
        return f"[{name}]"
    return f'"{name}"'


def qualified(t: SchemaObject, dialect: str) -> str:
    keep = t.schema_name and dialect in ("postgresql", "mssql")
    parts = [t.schema_name, t.name] if keep else [t.name]
    return ".".join(quote(p, dialect) for p in parts if p)


def bucket(expr: str, grain: str, dialect: str) -> str:
    if dialect == "postgresql":
        return f"date_trunc('{grain}', {expr})::date"
    if dialect == "mysql":
        return {
            "day": f"DATE({expr})",
            "week": f"DATE_SUB(DATE({expr}), INTERVAL WEEKDAY({expr}) DAY)",
            "month": f"DATE_FORMAT({expr}, '%Y-%m-01')",
            "quarter": f"MAKEDATE(YEAR({expr}), 1) + INTERVAL QUARTER({expr}) - 1 QUARTER",
            "year": f"YEAR({expr})",
        }[grain]
    if dialect == "mssql":
        return {
            "day": f"CAST({expr} AS date)",
            "week": f"DATEADD(week, DATEDIFF(week, 0, {expr}), 0)",
            "month": f"DATEFROMPARTS(YEAR({expr}), MONTH({expr}), 1)",
            "quarter": f"DATEFROMPARTS(YEAR({expr}), (DATEPART(quarter, {expr}) - 1) * 3 + 1, 1)",
            "year": f"YEAR({expr})",
        }[grain]
    return {
        "day": f"date({expr})",
        "week": f"date({expr}, 'weekday 0', '-6 days')",
        "month": f"strftime('%Y-%m', {expr})",
        "quarter": f"strftime('%Y', {expr}) || '-Q' || "
        f"((cast(strftime('%m', {expr}) AS integer) + 2) / 3)",
        "year": f"strftime('%Y', {expr})",
    }[grain]


def date_literal(d: dt.date, dialect: str) -> str:
    return f"DATE '{d.isoformat()}'" if dialect == "postgresql" else f"'{d.isoformat()}'"


def to_sql(g: Graph, intent: Intent, dialect: str) -> str:
    main = intent.table
    aliases: dict[str, str] = {main.qualified: "t0"}
    joins: list[str] = []

    def alias_for(t: SchemaObject) -> str:
        if t.qualified in aliases:
            return aliases[t.qualified]
        path = g.path(main, t)
        if path is None:
            raise CannotAnswerError(f"no path from {main.name} to {t.name}")
        for src, src_col, dst, dst_col in path:
            if dst.qualified in aliases:
                continue
            a = f"t{len(aliases)}"
            aliases[dst.qualified] = a
            joins.append(
                f"JOIN {qualified(dst, dialect)} {a} ON {a}.{quote(dst_col, dialect)} = "
                f"{aliases[src.qualified]}.{quote(src_col, dialect)}"
            )
        return aliases[t.qualified]

    def col(ref: Ref) -> str:
        return f"{alias_for(ref.table)}.{quote(ref.column.name, dialect)}"

    select: list[str] = []
    group: list[str] = []
    order: list[str] = []
    where: list[str] = []
    measure = "row_count"
    if intent.metric == "list":
        select = [f"t0.{quote(c.name, dialect)}" for c in list(main.columns)[:8]]
        if intent.order_by_date and intent.date_ref is not None:
            order.append(f"{col(intent.date_ref)} {(intent.order or 'desc').upper()}")
    else:
        if intent.grain and intent.date_ref is not None:
            expr = bucket(col(intent.date_ref), intent.grain, dialect)
            select.append(f"{expr} AS {intent.grain}")
            group.append(expr)
        elif intent.group is not None:
            gname = intent.group.column.name
            if gname in ("name", "title", "label") and intent.group.table is not main:
                gname = f"{stem(intent.group.table.name.lower())}_{gname}"
            select.append(f"{col(intent.group)} AS {quote(gname, dialect)}")
            group.append(col(intent.group))
        if intent.metric == "count" or intent.metric_ref is None:
            measure = f"{stem(main.name.lower())}_count"
            select.append(f"COUNT(*) AS {measure}")
        else:
            fn = intent.metric.upper()
            prefix = {"sum": "total", "avg": "average", "max": "max", "min": "min"}[intent.metric]
            column = intent.metric_ref.column.name
            measure = intent.label or (
                column if column.startswith(prefix) else f"{prefix}_{column}"
            )
            expr = f"{fn}({col(intent.metric_ref)})"
            if intent.metric in ("avg", "sum"):
                expr = f"ROUND({expr}, 2)"
            select.append(f"{expr} AS {measure}")
        if group:
            order.append(
                f"{intent.grain} ASC"
                if intent.grain
                else f"{measure} {(intent.order or 'desc').upper()}"
            )
    if intent.date_ref is not None and intent.start is not None:
        where.append(f"{col(intent.date_ref)} >= {date_literal(intent.start, dialect)}")
    if intent.date_ref is not None and intent.end is not None:
        where.append(f"{col(intent.date_ref)} < {date_literal(intent.end, dialect)}")
    for ref, value in intent.filters:
        where.append(f"{col(ref)} = '{value.replace(chr(39), chr(39) * 2)}'")
    limited = intent.limit is not None and (bool(group) or intent.metric == "list")
    top = f"TOP {intent.limit} " if limited and dialect == "mssql" else ""
    sql = f"SELECT {top}{', '.join(select)}\nFROM {qualified(main, dialect)} t0"
    for j in joins:
        sql += f"\n{j}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    if group:
        sql += "\nGROUP BY " + ", ".join(group)
    if order:
        sql += "\nORDER BY " + ", ".join(order)
    if limited and dialect != "mssql":
        sql += f"\nLIMIT {intent.limit}"
    return sql


def destructive(g: Graph, q: str, dialect: str) -> str | None:
    """What a model talked into writing would write. The guard must refuse it."""
    if m := re.search(r"\b(?:drop|truncate)\s+(?:the\s+)?(?:table\s+)?([a-z_]+)", q):
        t = g.find(m[1])
        return f"DROP TABLE {qualified(t, dialect) if t else m[1]}"
    if re.search(r"\b(?:delete|remove|erase)\b", q):
        for word in words(q):
            if t := g.find(word):
                sql = f"DELETE FROM {qualified(t, dialect)}"
                for ref, value in value_filters(g, q):
                    if ref.table is t:
                        sql += f" WHERE {quote(ref.column.name, dialect)} = '{value}'"
                        break
                return sql
    if re.search(r"\b(?:update|set|change)\b", q):
        for word in words(q):
            if (t := g.find(word)) and (
                nums := [c for c in t.columns if is_numeric(c) and not is_key(t, c)]
            ):
                return f"UPDATE {qualified(t, dialect)} SET {quote(nums[0].name, dialect)} = 0"
    return None


def plan(
    tables: list[SchemaObject], question: str, dialect: str, today: dt.date | None = None
) -> Plan:
    if not tables:
        return Plan(None, "The prompt contained no tables, so there is nothing to query.")
    g = Graph(tables)
    if bad := destructive(g, question.lower(), dialect):
        return Plan(bad, "Statement written as asked.")
    try:
        return Plan(to_sql(g, parse(g, question, today or dt.date.today()), dialect), "")
    except CannotAnswerError:
        names = ", ".join(t.name for t in tables[:6])
        return Plan(
            None,
            "I could not match this question to the tables in this database. Name a table or "
            f"a column, for example: how many rows are in {tables[0].name}? Tables: {names}.",
        )
