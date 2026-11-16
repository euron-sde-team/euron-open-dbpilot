"""The prompts DBPilot sends a model, and reading its answers back.

Every task names itself on the system message's first line (``TASK: generate``,
``TASK: explain``, ``TASK: diagnose``) so the deterministic mock model and a person reading
a log can tell them apart. The schema block is rendered from the connection's real
discovered schema only (AC6): the model is never told about a table or column the
discovery did not return. It is plain text a model reads easily and the mock parses back
(``parse_block``), so both always agree on what the prompt contained::

    Tables:
    - public.orders (table, ~6012 rows)
        id integer PK
        customer_id integer -> public.customers.id
        status varchar(20) values: delivered, shipped, cancelled
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any

from dbpilot.ai.providers import Message
from dbpilot.schema.schemas import SchemaColumn, SchemaForeignKey, SchemaObject

HEADER = "Tables:"
_WORD = re.compile(r"[a-z0-9]+")
_TABLE_LINE = re.compile(r"^- (?P<name>\S+) \((?P<kind>table|view)(?:, ~(?P<rows>\d+) rows)?\)$")
_COLUMN_LINE = re.compile(
    r"^ {4}(?P<name>\S+) (?P<type>.+?)(?P<pk> PK)?(?: -> (?P<ref>\S+))?"
    r"(?: values: (?P<values>.*))?$"
)

GENERATE_RULES = """You turn questions about a database into one SQL query.
Rules:
- Answer with one JSON object and nothing else: {"sql": "...", "explanation": "..."}.
- The query is exactly one read-only SELECT (CTEs, joins, subqueries and window functions
  are fine). Never write INSERT, UPDATE, DELETE, DDL, or more than one statement: DBPilot
  refuses anything else and never runs it.
- Use only the tables and columns listed below, with the dialect's syntax and functions.
- Prefer readable column aliases. Add a row limit when listing rows.
- "explanation" says in one or two plain sentences what the query returns.
- If the question cannot be answered from these tables, answer
  {"sql": null, "explanation": "<why, and what could be asked instead>"}."""

EXPLAIN_RULES = """You explain SQL statements to people who do not read SQL.
Answer with one JSON object and nothing else: {"explanation": "..."}.
Say what the statement returns or changes, from which tables, which rows it keeps, how it
groups and sorts, in two to five plain sentences. Do not run or rewrite it. If it changes
data instead of reading it, say so plainly."""

DIAGNOSE_RULES = """You help people fix a SQL statement that a database rejected.
Answer with one JSON object and nothing else: {"diagnosis": "...", "suggestion": "..."}.
"diagnosis" says in plain words why the database refused it; "suggestion" says what to
change, using only the tables and columns listed below. Never suggest a write."""


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def stem(word: str) -> str:
    """A crude singular: orders -> order, categories -> category, salaries -> salary."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def stems(text: str) -> set[str]:
    out: set[str] = set()
    for w in words(text):
        out.add(w)
        out.add(stem(w))
    return out


def relevance(table: SchemaObject, question: str) -> int:
    """How strongly a question points at a table: its name counts most, then columns."""
    q = stems(question)
    score = 10 * len({stem(p) for p in words(table.name)} & q)
    for c in table.columns:
        score += 2 * len({stem(p) for p in words(c.name)} & q)
        if any(v.lower() in question.lower() for v in c.sample_values if len(v) > 2):
            score += 4
    return score


def render_table(t: SchemaObject) -> str:
    rows = f", ~{t.row_count} rows" if t.row_count is not None else ""
    kind = "view" if t.kind == "view" else "table"
    lines = [f"- {t.qualified} ({kind}{rows})"]
    refs: dict[str, str] = {}
    for fk in t.foreign_keys:
        if len(fk.columns) == 1 and len(fk.ref_columns) == 1:
            refs[fk.columns[0]] = f"{fk.ref_table}.{fk.ref_columns[0]}"
    for c in t.columns:
        line = f"    {c.name} {c.type.lower()}"
        if c.primary_key:
            line += " PK"
        if c.name in refs:
            line += f" -> {refs[c.name]}"
        if c.sample_values:
            line += " values: " + ", ".join(c.sample_values)
        lines.append(line)
    return "\n".join(lines)


def select_tables(tables: list[SchemaObject], question: str, max_chars: int) -> list[SchemaObject]:
    """All tables when they fit, otherwise the most relevant first (plus the tables they
    reference) until the budget is spent."""
    rendered = {t.qualified: render_table(t) for t in tables}
    if sum(len(r) + 1 for r in rendered.values()) <= max_chars:
        return list(tables)
    by_name = {t.qualified: t for t in tables}
    ranked = sorted(tables, key=lambda t: (-relevance(t, question), t.qualified))
    chosen: dict[str, SchemaObject] = {}
    used = 0
    for t in ranked:
        group = [t] + [by_name[fk.ref_table] for fk in t.foreign_keys if fk.ref_table in by_name]
        for g in group:
            size = len(rendered[g.qualified]) + 1
            if g.qualified in chosen or used + size > max_chars:
                continue
            chosen[g.qualified] = g
            used += size
        if used >= max_chars:
            break
    return list(chosen.values())


def render(tables: list[SchemaObject], question: str, max_chars: int) -> str:
    chosen = select_tables(tables, question, max_chars)
    return HEADER + "\n" + "\n".join(render_table(t) for t in chosen)


def parse_block(text: str) -> list[SchemaObject]:
    """The tables in a prompt that ``render`` wrote (anything else is ignored)."""
    if HEADER not in text:
        return []

    @dataclass
    class Parsed:
        name: str
        kind: str
        rows: int | None
        columns: list[SchemaColumn] = field(default_factory=list)
        fks: list[SchemaForeignKey] = field(default_factory=list)

    out: list[Parsed] = []
    for line in text.split(HEADER, 1)[1].splitlines():
        if m := _TABLE_LINE.match(line):
            out.append(Parsed(m["name"], m["kind"], int(m["rows"]) if m["rows"] else None))
        elif out and (m := _COLUMN_LINE.match(line)):
            col = SchemaColumn(
                name=m["name"],
                type=m["type"],
                primary_key=bool(m["pk"]),
                sample_values=[v.strip() for v in (m["values"] or "").split(",") if v.strip()],
            )
            out[-1].columns.append(col)
            if m["ref"]:
                ref_table, _, ref_col = m["ref"].rpartition(".")
                out[-1].fks.append(
                    SchemaForeignKey(columns=[col.name], ref_table=ref_table, ref_columns=[ref_col])
                )
        elif out and not line.startswith(" "):
            break
    result = []
    for p in out:
        schema, _, name = p.name.rpartition(".")
        result.append(
            SchemaObject(
                schema_name=schema or None,
                name=name,
                kind=p.kind,
                row_count=p.rows,
                columns=p.columns,
                foreign_keys=p.fks,
            )
        )
    return result


def generate_messages(
    *,
    dialect: str,
    tables: list[SchemaObject],
    question: str,
    max_schema_chars: int,
    today: dt.date | None = None,
    connection_note: str = "",
) -> tuple[Message, ...]:
    block = render(tables, question, max_schema_chars)
    system = "\n".join(
        [
            "TASK: generate",
            f"Dialect: {dialect}",
            f"Today: {(today or dt.date.today()).isoformat()}",
            *([f"About this database: {connection_note}"] if connection_note else []),
            "",
            GENERATE_RULES,
            "",
            block,
        ]
    )
    return (Message("system", system), Message("user", f"Question: {question}"))


def explain_messages(*, dialect: str, sql: str) -> tuple[Message, ...]:
    system = "\n".join(["TASK: explain", f"Dialect: {dialect}", "", EXPLAIN_RULES])
    return (Message("system", system), Message("user", f"```sql\n{sql.strip()}\n```"))


def diagnose_messages(
    *, dialect: str, sql: str, error: str, tables: list[SchemaObject], max_schema_chars: int
) -> tuple[Message, ...]:
    block = render(tables, sql, max_schema_chars)
    system = "\n".join(["TASK: diagnose", f"Dialect: {dialect}", "", DIAGNOSE_RULES, "", block])
    return (
        Message("system", system),
        Message("user", f"```sql\n{sql.strip()}\n```\nError: {error.strip()}"),
    )


_FENCE = re.compile(r"```(?:json|sql)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def json_object(text: str) -> dict[str, Any] | None:
    candidates = [text.strip(), *[m.strip() for m in _FENCE.findall(text)]]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            value = json.loads(c)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


@dataclass(frozen=True)
class Answer:
    sql: str | None
    explanation: str


def parse_answer(text: str) -> Answer:
    """The SQL and explanation in a model's answer (None SQL when it declined). Accepts the
    JSON object, the object inside a Markdown fence, or a bare ```sql fence."""
    obj = json_object(text)
    if obj is not None:
        sql = obj.get("sql")
        return Answer(
            sql=str(sql).strip() if isinstance(sql, str) and sql.strip() else None,
            explanation=str(obj.get("explanation") or "").strip(),
        )
    fences = re.findall(r"```sql\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fences:
        rest = re.sub(r"```sql\s*\n.*?```", "", text, flags=re.DOTALL | re.IGNORECASE)
        return Answer(sql=fences[0].strip(), explanation=rest.strip())
    stripped = text.strip()
    if re.match(r"(?is)^(with|select|insert|update|delete|drop|create|alter)\b", stripped):
        return Answer(sql=stripped, explanation="")
    return Answer(sql=None, explanation=stripped[:1000])


def parse_field(text: str, name: str) -> str:
    obj = json_object(text)
    if obj is not None and obj.get(name):
        return str(obj[name]).strip()
    return text.strip()
