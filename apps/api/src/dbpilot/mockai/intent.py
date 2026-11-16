"""The deterministic mock model's question parser: a question and the schema block of the
prompt become an ``Intent`` (what to measure, over which table, grouped and filtered how).

It understands what the demo and the tests ask: counts, sums, averages, minimums and
maximums; grouping by a column, by a related table (through foreign keys) or by a time
grain; calendar filters ("in 2025", "last year", "last 30 days"); filters on sampled values
("shipped orders"); top and bottom N; listings. It is a test fixture with good manners, not
a language model: a question it cannot map to the schema raises ``CannotAnswerError``.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from dbpilot.ai.prompt import stem, stems, words
from dbpilot.schema.schemas import SchemaColumn, SchemaObject

NUMERIC = ("int", "numeric", "decimal", "float", "double", "real", "money", "number")
TEMPORAL = ("date", "time")
GRAINS = {
    "day": "day",
    "daily": "day",
    "week": "week",
    "weekly": "week",
    "month": "month",
    "monthly": "month",
    "quarter": "quarter",
    "quarterly": "quarter",
    "year": "year",
    "yearly": "year",
    "annual": "year",
    "annually": "year",
}
MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}
SUM_WORDS = {"total", "sum", "revenue", "sales", "spend", "spent", "spending", "income", "sold"}
AVG_WORDS = {"average", "avg", "mean", "typical"}
MAX_WORDS = {"highest", "maximum", "max", "largest", "biggest", "expensive"}
MIN_WORDS = {"lowest", "minimum", "min", "smallest", "cheapest"}
COUNT_PHRASES = ("how many", "number of", "count of", "count the", "count ")
MONEY_WORDS = {"revenue", "sales", "spend", "spent", "spending", "income", "sold", "value"}
MONEY_COLUMNS = ("total_amount", "amount", "line_total", "revenue", "total", "price", "value")
LABEL_COLUMNS = ("name", "title", "full_name", "label", "company", "email", "code")


class CannotAnswerError(Exception):
    pass


@dataclass
class Ref:
    table: SchemaObject
    column: SchemaColumn

    @property
    def key(self) -> tuple[str, str]:
        return self.table.qualified, self.column.name


@dataclass
class Intent:
    table: SchemaObject
    metric: str = "count"  # count, sum, avg, max, min, list
    metric_ref: Ref | None = None
    group: Ref | None = None
    grain: str | None = None
    date_ref: Ref | None = None
    start: dt.date | None = None
    end: dt.date | None = None
    filters: list[tuple[Ref, str]] = field(default_factory=list)
    order: str | None = None  # desc or asc
    order_by_date: bool = False
    limit: int | None = None
    label: str | None = None


def mentions(t: SchemaObject, qw: set[str]) -> bool:
    parts = {stem(p) for p in words(t.name)}
    return bool(parts) and (parts <= qw or t.name.lower() in qw)


def is_numeric(c: SchemaColumn) -> bool:
    t = c.type.lower()
    return any(n in t for n in NUMERIC) and not any(x in t for x in ("interval", "point"))


def is_temporal(c: SchemaColumn) -> bool:
    t = c.type.lower()
    return any(n in t for n in TEMPORAL) and "zone" not in c.name.lower()


def is_key(t: SchemaObject, c: SchemaColumn) -> bool:
    fk_cols = {col for fk in t.foreign_keys for col in fk.columns}
    return c.primary_key or c.name in fk_cols or c.name == "id" or c.name.endswith("_id")


def label_of(t: SchemaObject) -> SchemaColumn:
    names = {c.name.lower(): c for c in t.columns}
    for want in LABEL_COLUMNS:
        if want in names:
            return names[want]
    for c in t.columns:
        if not is_numeric(c) and not is_temporal(c) and not is_key(t, c):
            return c
    return t.columns[0]


def date_of(t: SchemaObject) -> SchemaColumn | None:
    temporal = [c for c in t.columns if is_temporal(c)]
    for hint in ("order_date", "date", "created", "issued", "hire", "at", "on"):
        for c in temporal:
            if hint in c.name.lower():
                return c
    return temporal[0] if temporal else None


class Graph:
    """Many-to-one paths along foreign keys: from a fact table to the tables it points at."""

    def __init__(self, tables: list[SchemaObject]) -> None:
        self.tables = tables
        self.by_name = {t.qualified: t for t in tables}
        for t in tables:
            self.by_name.setdefault(t.name, t)

    def find(self, word: str) -> SchemaObject | None:
        s = stem(word)
        for t in self.tables:
            if stem(t.name.lower()) == s or t.name.lower() == word:
                return t
        for t in self.tables:
            parts = [stem(p) for p in words(t.name)]
            if len(parts) > 1 and parts[-1] == s and t.kind == "table":
                return t
        return None

    def path(
        self, src: SchemaObject, dst: SchemaObject, depth: int = 2
    ) -> list[tuple[SchemaObject, str, SchemaObject, str]] | None:
        if src.qualified == dst.qualified:
            return []
        if depth == 0:
            return None
        best: list[tuple[SchemaObject, str, SchemaObject, str]] | None = None
        for fk in src.foreign_keys:
            nxt = self.by_name.get(fk.ref_table) or self.by_name.get(fk.ref_table.split(".")[-1])
            if nxt is None or len(fk.columns) != 1:
                continue
            rest = self.path(nxt, dst, depth - 1)
            if rest is not None:
                cand = [(src, fk.columns[0], nxt, fk.ref_columns[0]), *rest]
                if best is None or len(cand) < len(best):
                    best = cand
        return best


def has_word(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.lower())}(?![a-z0-9])", text) is not None


def time_filter(q: str, today: dt.date) -> tuple[dt.date | None, dt.date | None] | None:
    year = today.year
    if m := re.search(r"\b(?:in|during|for|of)\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", q):
        y = int(m[2]) if m[2] else year
        start = dt.date(y, MONTHS[m[1]], 1)
        return start, dt.date(y + (start.month == 12), start.month % 12 + 1, 1)
    if m := re.search(r"\bsince\s+(\d{4})\b", q):
        return dt.date(int(m[1]), 1, 1), None
    if m := re.search(r"\b(?:in|during|for|of|from)\s+(\d{4})\b", q) or re.search(
        r"\b(20\d\d|19\d\d)\b", q
    ):
        y = int(m[1])
        return dt.date(y, 1, 1), dt.date(y + 1, 1, 1)
    if m := re.search(r"\b(?:last|past|previous)\s+(\d+)\s+days?\b", q):
        return today - dt.timedelta(days=int(m[1])), today + dt.timedelta(days=1)
    if "last year" in q or "previous year" in q:
        return dt.date(year - 1, 1, 1), dt.date(year, 1, 1)
    if "this year" in q or "year to date" in q:
        return dt.date(year, 1, 1), today + dt.timedelta(days=1)
    first = today.replace(day=1)
    if "last month" in q or "previous month" in q:
        return (first - dt.timedelta(days=1)).replace(day=1), first
    if "this month" in q:
        return first, today + dt.timedelta(days=1)
    return None


def limit_of(q: str) -> tuple[str | None, int | None]:
    if m := re.search(r"\b(?:top|best|first|largest|biggest|highest)\s+(\d+)\b", q):
        return "desc", int(m[1])
    if m := re.search(r"\b(?:bottom|worst|lowest|smallest|last)\s+(\d+)\b(?!\s+days?)", q):
        return "asc", int(m[1])
    if re.search(r"\b(?:which|what)\b.*\b(?:most|highest|largest|biggest|best)\b", q):
        return "desc", 1
    if re.search(r"\b(?:which|what)\b.*\b(?:least|fewest|lowest|smallest|worst)\b", q):
        return "asc", 1
    if re.search(r"\btop\b", q):
        return "desc", 10
    return None, None


def value_filters(g: Graph, q: str) -> list[tuple[Ref, str]]:
    out: list[tuple[Ref, str]] = []
    for t in g.tables:
        for c in t.columns:
            for v in c.sample_values:
                if (
                    len(v) > 2
                    and has_word(q, v.lower())
                    and not any(r.key == (t.qualified, c.name) for r, _ in out)
                ):
                    out.append((Ref(t, c), v))
    return out


def metric_of(q: str, qw: set[str]) -> str | None:
    if any(p in q for p in COUNT_PHRASES):
        return "count"
    if qw & AVG_WORDS:
        return "avg"
    if qw & SUM_WORDS:
        return "sum"
    if qw & MAX_WORDS and not re.search(r"\b(?:which|what|top)\b", q):
        return "max"
    if qw & MIN_WORDS and not re.search(r"\b(?:which|what|bottom)\b", q):
        return "min"
    if re.search(r"\b(?:list|show|display|give me|latest|recent|newest|oldest)\b", q):
        return "list"
    return None


def group_words(q: str) -> list[str]:
    found: list[str] = []
    for m in re.finditer(
        r"\b(?:by|per|each|for each|across|grouped by)\s+([a-z_]+)(?:\s+([a-z_]+))?", q
    ):
        found.append(" ".join(p for p in (m[1], m[2]) if p))
    return found


def grain_of(q: str, qw: set[str]) -> str | None:
    for gw in group_words(q):
        first = gw.split()[0]
        if first in GRAINS:
            return GRAINS[first]
    for w in ("monthly", "weekly", "daily", "quarterly", "yearly", "annual", "annually"):
        if w in qw:
            return GRAINS[w]
    if re.search(r"\b(?:over time|trend|each month|every month)\b", q):
        return "month"
    return None


def _money_rank(c: SchemaColumn) -> int:
    name = c.name.lower()
    for i, want in enumerate(MONEY_COLUMNS):
        if name == want or name.endswith(want):
            return i
    return len(MONEY_COLUMNS)


def numeric_candidates(g: Graph, qw: set[str]) -> list[Ref]:
    plain = qw - set(GRAINS)
    table_words = {stem(p) for t in g.tables for p in words(t.name)}
    ranked: list[tuple[tuple[int, int, int, int], Ref]] = []
    for t in g.tables:
        in_named = mentions(t, plain)
        for c in t.columns:
            if not is_numeric(c) or is_key(t, c):
                continue
            parts = {stem(p) for p in words(c.name)} - {stem(p) for p in words(t.name)}
            named = bool(parts & plain) and not parts <= table_words
            money = bool(plain & MONEY_WORDS) and _money_rank(c) < len(MONEY_COLUMNS)
            if not named and not money:
                continue
            ranked.append(
                (
                    (
                        0 if named else 1,
                        _money_rank(c) if money else len(MONEY_COLUMNS),
                        0 if in_named else 1,
                        1 if t.kind == "view" else 0,
                    ),
                    Ref(t, c),
                )
            )
    ranked.sort(key=lambda x: x[0])
    return [r for _, r in ranked]


def resolve_group(g: Graph, gw: str, main: SchemaObject | None) -> Ref | None:
    parts = gw.split()
    if len(parts) == 2 and (t := g.find(parts[0])):
        for c in t.columns:
            if stem(c.name.lower()) == stem(parts[1]):
                return Ref(t, c)
    for word in (gw.replace(" ", "_"), parts[0]):
        tables = ([main] if main else []) + [t for t in g.tables if main is None or t is not main]
        for t in tables:
            for c in t.columns:
                if c.name.lower() == word or stem(c.name.lower()) == stem(word):
                    return Ref(t, c)
        if t2 := g.find(word):
            return Ref(t2, label_of(t2))
    return None


def date_ref(g: Graph, main: SchemaObject) -> Ref | None:
    if (c := date_of(main)) is not None:
        return Ref(main, c)
    for fk in main.foreign_keys:
        t = g.by_name.get(fk.ref_table) or g.by_name.get(fk.ref_table.split(".")[-1])
        if t is not None and (c := date_of(t)) is not None:
            return Ref(t, c)
    return None


def _prefer(values: list[tuple[Ref, str]], main: SchemaObject) -> list[tuple[Ref, str]]:
    out: dict[str, tuple[Ref, str]] = {}
    for ref, value in values:
        kept = out.get(value.lower())
        if kept is None or (ref.table is main and kept[0].table is not main):
            out[value.lower()] = (ref, value)
    return list(out.values())


def parse(g: Graph, question: str, today: dt.date) -> Intent:
    q = " ".join(question.lower().replace("?", " ").replace(",", " ").split())
    qw = stems(q)
    mentioned = [t for t in g.tables if mentions(t, qw)]
    metric = metric_of(q, qw)
    grain = grain_of(q, qw)
    time = time_filter(q, today)
    order, limit = limit_of(q)
    values = value_filters(g, q)
    gwords = [gw for gw in group_words(q) if gw.split()[0] not in GRAINS]
    top_entity: SchemaObject | None = None
    if m := re.search(r"\b(?:top|bottom|best|worst)\s+(?:\d+\s+)?([a-z_]+)", q):
        top_entity = g.find(m[1])
    if m := re.search(r"\b(?:which|what)\s+([a-z_]+)", q):
        top_entity = top_entity or g.find(m[1])
        if top_entity is None and m[1] not in ("is", "are", "was", "were", "the"):
            gwords = [m[1], *gwords]
    metric_ref: Ref | None = None
    if metric in ("sum", "avg", "max", "min") or (metric is None and qw & MONEY_WORDS):
        metric = metric or "sum"
        cands = numeric_candidates(g, qw)
        target = top_entity
        if target is None and gwords and (ref := resolve_group(g, gwords[0], None)):
            target = ref.table
        if target is not None:
            reachable = [r for r in cands if g.path(r.table, target) is not None]
            cands = reachable or cands
        if cands:
            metric_ref = cands[0]
    if metric_ref is not None:
        main = metric_ref.table
    else:
        facts = [t for t in mentioned if t is not top_entity] or mentioned
        if not facts and values:
            facts = [values[0][0].table]
        if not facts:
            raise CannotAnswerError(question)
        main = facts[0]
        if top_entity is not None and len(facts) > 1:
            linked = [t for t in facts if g.path(t, top_entity)]
            main = linked[0] if linked else main
        metric = metric or ("count" if (gwords or top_entity or grain) else "list")
        if metric in ("sum", "avg", "max", "min"):
            nums = [c for c in main.columns if is_numeric(c) and not is_key(main, c)]
            if not nums:
                metric = "count"
            else:
                metric_ref = Ref(main, nums[0])
    intent = Intent(table=main, metric=metric or "count", metric_ref=metric_ref)
    if top_entity is not None and top_entity is not main and metric != "list":
        intent.group = Ref(top_entity, label_of(top_entity))
    for gw in gwords:
        if ref := resolve_group(g, gw, main):
            intent.group = ref
            break
    if grain:
        intent.grain = grain
        intent.date_ref = date_ref(g, main)
    if time is not None:
        intent.start, intent.end = time
        intent.date_ref = intent.date_ref or date_ref(g, main)
    if limit is not None:
        intent.order, intent.limit = order, limit
    if intent.metric == "list":
        if re.search(r"\b(?:latest|recent|newest)\b", q):
            intent.order, intent.order_by_date = "desc", True
            intent.date_ref = intent.date_ref or date_ref(g, main)
        elif re.search(r"\boldest\b", q):
            intent.order, intent.order_by_date = "asc", True
            intent.date_ref = intent.date_ref or date_ref(g, main)
        intent.limit = intent.limit or (int(m[1]) if (m := re.search(r"\b(\d+)\b", q)) else 50)
    intent.filters = _prefer(values, main)
    if intent.metric_ref is not None and intent.metric in ("sum", "avg"):
        for word in ("revenue", "sales", "spend", "income"):
            if word in qw:
                intent.label = word if intent.metric == "sum" else f"average_{word}"
                break
    return intent
