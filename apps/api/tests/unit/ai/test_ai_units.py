"""The AI assistant's pieces: AC6's identifier check, the prompt and its parsing, the
deterministic mock model, advice, and the provider adapters on their wire formats."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from dbpilot.ai import advice, prompt
from dbpilot.ai.describe import analyze, describe
from dbpilot.ai.identifiers import check_identifiers
from dbpilot.ai.providers import (
    AnthropicProvider,
    DisabledProvider,
    Message,
    MockProvider,
    OpenAIProvider,
    ProviderError,
    from_settings,
)
from dbpilot.core.config import Settings
from dbpilot.mockai.planner import plan
from dbpilot.mockai.responder import respond
from dbpilot.queries import guard
from dbpilot.schema.schemas import SchemaColumn, SchemaForeignKey, SchemaIndex, SchemaObject


def _t(
    name: str, cols: list[tuple[str, str]], schema: str | None = "public", **kw: Any
) -> SchemaObject:
    return SchemaObject(
        schema_name=schema,
        name=name,
        kind="table",
        columns=[SchemaColumn(name=n, type=t, primary_key=(n == "id")) for n, t in cols],
        **kw,
    )


TABLES = [
    _t(
        "customers",
        [
            ("id", "integer"),
            ("name", "varchar(120)"),
            ("country", "varchar(60)"),
            ("created_at", "date"),
        ],
        row_count=300,
    ),
    _t(
        "orders",
        [
            ("id", "integer"),
            ("customer_id", "integer"),
            ("status", "varchar(20)"),
            ("order_date", "date"),
            ("total_amount", "numeric(12,2)"),
        ],
        row_count=2500,
        foreign_keys=[
            SchemaForeignKey(
                name="fk", columns=["customer_id"], ref_table="public.customers", ref_columns=["id"]
            )
        ],
        indexes=[SchemaIndex(name="ix_orders_date", columns=["order_date"], unique=False)],
    ),
]


def _check(sql: str) -> Any:
    return check_identifiers(guard.check(sql, "postgresql").tree, TABLES)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name, country FROM customers",
        "SELECT c.name, o.total_amount FROM customers c JOIN orders o ON o.customer_id = c.id",
        "SELECT status, count(*) AS n FROM orders GROUP BY status ORDER BY n DESC",
        "WITH t AS (SELECT customer_id, sum(total_amount) AS spent FROM orders GROUP BY customer_id) SELECT t.spent FROM t",
        "SELECT x.n FROM (SELECT count(*) AS n FROM orders) x",
        "SELECT date_trunc('month', order_date) AS m FROM public.orders",
        "WITH s AS (SELECT * FROM orders) SELECT status FROM s",
    ],
)
def test_ac6_real_names_pass(sql: str) -> None:
    assert _check(sql).ok, sql


def test_ac6_fabricated_names_are_reported() -> None:
    r = _check("SELECT loyalty_points FROM customers")
    assert r.unknown_columns == ["loyalty_points"] and not r.ok
    assert _check("SELECT * FROM invoices").unknown_tables == ["invoices"]
    assert _check("SELECT o.discount FROM orders o").unknown_columns == ["o.discount"]
    assert _check("SELECT z.name FROM customers c").unknown_columns == ["z.name"]
    assert _check("SELECT x.missing FROM (SELECT count(*) AS n FROM orders) x").unknown_columns == [
        "x.missing"
    ]
    assert "columns loyalty_points" in _check("SELECT loyalty_points FROM customers").message()
    assert _check("SELECT * FROM other.customers").unknown_tables == ["other.customers"]


def test_ac6_an_alias_does_not_vouch_for_its_own_column() -> None:
    # The alias of an expression names its output; it is no evidence the column exists.
    assert _check("SELECT salary AS salary FROM customers").unknown_columns == ["salary"]
    assert _check(
        "SELECT x.salary FROM (SELECT salary AS salary FROM customers) x"
    ).unknown_columns == ["salary"]
    assert _check(
        "WITH t AS (SELECT salary AS pay FROM customers) SELECT pay FROM t"
    ).unknown_columns == ["salary"]
    assert _check("SELECT 1 AS salary, salary + 1 AS raise FROM customers").unknown_columns == [
        "salary"
    ]
    # Naming an output alias where SQL allows it stays fine.
    assert _check("SELECT count(*) AS n FROM orders HAVING n > 1 ORDER BY n").ok
    assert _check("SELECT name AS who FROM customers ORDER BY who").ok
    assert _check("WITH t AS (SELECT name AS who FROM customers) SELECT who FROM t ORDER BY who").ok
    # A recursive CTE reads its own columns inside its definition.
    assert _check(
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 10) SELECT n FROM r"
    ).ok
    assert _check(
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT bonus + 1 FROM r WHERE n < 10) SELECT n FROM r"
    ).unknown_columns == ["bonus"]


def test_the_schema_block_round_trips_and_respects_its_budget() -> None:
    block = prompt.render(TABLES, "orders by status", 10_000)
    parsed = prompt.parse_block("x\n" + block)
    assert [t.qualified for t in parsed] == ["public.customers", "public.orders"]
    assert parsed[1].foreign_keys[0].ref_table == "public.customers"
    tight = prompt.select_tables(TABLES, "orders", 200)
    assert tight[0].name == "orders"
    assert prompt.parse_block("no header") == []
    msgs = prompt.generate_messages(
        dialect="postgresql",
        tables=TABLES,
        question="q",
        max_schema_chars=5_000,
        connection_note="shop",
    )
    assert (
        msgs[0].content.startswith("TASK: generate")
        and "About this database: shop" in msgs[0].content
    )
    assert prompt.explain_messages(dialect="mysql", sql="SELECT 1")[0].content.startswith(
        "TASK: explain"
    )
    assert prompt.diagnose_messages(
        dialect="mysql", sql="x", error="e", tables=TABLES, max_schema_chars=5_000
    )[1].content.endswith("Error: e")


def test_answers_are_read_back_from_every_shape() -> None:
    assert prompt.parse_answer('{"sql": "SELECT 1", "explanation": "one"}') == prompt.Answer(
        "SELECT 1", "one"
    )
    assert prompt.parse_answer('```json\n{"sql": null, "explanation": "no"}\n```').sql is None
    fenced = prompt.parse_answer("Here:\n```sql\nSELECT 2\n```\nthat is it")
    assert fenced.sql == "SELECT 2" and "that is it" in fenced.explanation
    assert prompt.parse_answer("DELETE FROM orders").sql == "DELETE FROM orders"
    assert prompt.parse_answer("I cannot help").sql is None
    assert prompt.parse_field('{"diagnosis": "d"}', "diagnosis") == "d"
    assert prompt.parse_field("plain text", "diagnosis") == "plain text"


def test_the_mock_model_plans_from_the_schema_and_can_be_talked_into_writes() -> None:
    assert plan(TABLES, "how many orders per status", "postgresql").sql.startswith(
        "SELECT t0.status"
    )  # type: ignore[union-attr]
    revenue = plan(TABLES, "total revenue by country", "mysql").sql
    assert revenue is not None and "JOIN" in revenue and "LIMIT" not in revenue
    top = plan(TABLES, "top 5 customers by revenue", "mssql").sql
    assert top is not None and top.startswith("SELECT TOP 5")
    monthly = plan(TABLES, "monthly orders in 2025", "sqlite").sql
    assert monthly is not None and "strftime('%Y-%m'" in monthly and "'2025-01-01'" in monthly
    assert plan(TABLES, "delete all orders", "postgresql").sql == "DELETE FROM public.orders"
    assert (
        plan(TABLES, "drop the customers table", "postgresql").sql == "DROP TABLE public.customers"
    )
    assert plan(TABLES, "what is the weather", "postgresql").sql is None
    assert plan([], "anything", "postgresql").sql is None
    for q in (
        "latest orders",
        "average order amount last year",
        "orders this month",
        "how many customers since 2024",
    ):
        sql = plan(TABLES, q, "postgresql").sql
        assert sql is not None and guard.is_read_only(sql, "postgresql")[0], (q, sql)


def test_the_responder_answers_each_task() -> None:
    system = "TASK: generate\nDialect: postgresql\nToday: 2026-06-15\n\n" + prompt.render(
        TABLES, "", 10_000
    )
    out = json.loads(respond(system, [("user", "Question: how many customers")]))
    assert out["sql"].startswith("SELECT COUNT(*)") and out["explanation"]
    explained = json.loads(
        respond("TASK: explain\nDialect: mysql", [("user", "```sql\nSELECT 1\n```")])
    )
    assert "It reads" in explained["explanation"]
    diag = json.loads(
        respond(
            "TASK: diagnose\nDialect: postgresql\n\n" + prompt.render(TABLES, "", 10_000),
            [
                (
                    "user",
                    '```sql\nSELECT nme FROM customers\n```\nError: column "nme" does not exist',
                )
            ],
        )
    )
    assert "name" in diag["diagnosis"]
    assert "only answers" in json.loads(respond("hello", []))["answer"]


def test_describe_and_analyze() -> None:
    facts = analyze(
        "SELECT c.country, sum(o.total_amount) FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.status = 'x' GROUP BY c.country HAVING sum(o.total_amount) > 5 ORDER BY 2 DESC LIMIT 3",
        "postgresql",
    )
    assert facts.read_only and facts.joins and facts.group_by == ["c.country"] and facts.limit == 3
    assert any("after grouping" in f for f in facts.filters)
    assert "every row" in describe("DELETE FROM orders", "postgresql")
    assert "not a statement DBPilot can read" in describe("SELEC (", "postgresql")
    assert "window function" in describe("SELECT row_number() OVER () FROM t", "postgresql")


def test_index_advice_and_diagnosis() -> None:
    tables = [
        _t(
            "orders",
            [("id", "integer"), ("customer_id", "integer"), ("status", "text")],
            row_count=50_000,
            foreign_keys=[
                SchemaForeignKey(
                    columns=["customer_id"], ref_table="public.customers", ref_columns=["id"]
                )
            ],
        ),
        _t("customers", [("id", "integer"), ("name", "text")], row_count=10),
    ]
    found = advice.index_suggestions(
        tables,
        "postgresql",
        [
            "SELECT * FROM orders WHERE status = 'x'",
            "SELECT * FROM orders o WHERE o.status = 'y'",
            "bad sql (",
        ],
    )
    sources = [(s.source, s.columns) for s in found]
    assert ("foreign_key", ["customer_id"]) in sources and ("query_history", ["status"]) in sources
    assert (
        found[0].ddl == 'CREATE INDEX "ix_orders_customer_id" ON "public"."orders" ("customer_id")'
    )
    cases = {
        'relation "custmers" does not exist': ("unknown_table", "customers"),
        "Unknown column 'nmae' in 'field list'": ("unknown_column", "name"),
        "cannot execute INSERT in a read-only transaction": ("read_only", None),
        "permission denied for table x": ("permission", None),
        "canceling statement due to statement timeout": ("timeout", None),
        'column "x" must appear in the GROUP BY clause': ("group_by", None),
        'column reference "id" is ambiguous': ("ambiguous", None),
        "operator does not exist: integer = text": ("type_mismatch", None),
        'syntax error at or near "FORM"': ("syntax", None),
        "something odd": ("other", None),
    }
    for error, (category, similar) in cases.items():
        d = advice.diagnose("x", error, tables)
        assert d.category == category, error
        if similar:
            assert similar in d.similar


async def test_the_provider_adapters_speak_their_wire_formats() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"url": str(request.url), "headers": dict(request.headers), "body": body})
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(
                200,
                json={
                    "model": "m1",
                    "choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "c1",
                "content": [{"type": "text", "text": "yo"}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    msgs = (Message("system", "S"), Message("user", "U"))
    out = await OpenAIProvider(http, "http://p/v1", "sk-1", "m", 5).complete(msgs, max_tokens=10)
    assert (
        out.text == "hi"
        and out.input_tokens == 3
        and seen[-1]["headers"]["authorization"] == "Bearer sk-1"
    )
    ant = await AnthropicProvider(http, "http://a", "key-2", "c", 5).complete(msgs, max_tokens=10)
    assert (
        ant.text == "yo"
        and seen[-1]["body"]["system"] == "S"
        and seen[-1]["headers"]["x-api-key"] == "key-2"
    )
    assert seen[-1]["body"]["messages"] == [{"role": "user", "content": "U"}]
    await http.aclose()


async def test_provider_errors_are_classified_and_retried() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        if calls["n"] == 2:
            return httpx.Response(200, json={"choices": []})
        return httpx.Response(401, text="no")

    http = httpx.AsyncClient(transport=httpx.MockTransport(flaky))
    p = OpenAIProvider(http, "http://p/v1", "", "m", 5)
    with pytest.raises(ProviderError) as err:
        await p.complete((Message("user", "x"),), max_tokens=5)
    assert err.value.retryable is True  # the empty answer after one retry
    with pytest.raises(ProviderError) as err2:
        await p.complete((Message("user", "x"),), max_tokens=5)
    assert err2.value.code == "ai_auth" and err2.value.retryable is False

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    http2 = httpx.AsyncClient(transport=httpx.MockTransport(down))
    with pytest.raises(ProviderError) as err3:
        await OpenAIProvider(http2, "http://p/v1", "", "m", 5).complete(
            (Message("user", "x"),), max_tokens=5
        )
    assert err3.value.code == "ai_unreachable"
    await http.aclose()
    await http2.aclose()


async def test_providers_are_chosen_by_settings() -> None:
    http = httpx.AsyncClient()
    assert isinstance(from_settings(Settings(ai_provider="mock"), http), MockProvider)
    assert isinstance(from_settings(Settings(ai_provider="none"), http), DisabledProvider)
    assert isinstance(
        from_settings(Settings(ai_provider="openai", ai_api_key="k"), http), OpenAIProvider
    )
    assert isinstance(
        from_settings(Settings(ai_provider="anthropic", ai_api_key="k"), http), AnthropicProvider
    )
    with pytest.raises(ProviderError) as err:
        await DisabledProvider().complete((Message("user", "x"),), max_tokens=1)
    assert err.value.code == "ai_disabled"
    mock = await MockProvider().complete(
        (Message("system", "nothing"), Message("user", "x")), max_tokens=1
    )
    assert mock.model == "dbpilot-mock" and mock.input_tokens > 0
    await http.aclose()
