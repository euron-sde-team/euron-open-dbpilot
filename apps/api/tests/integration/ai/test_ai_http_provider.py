"""The AI assistant through a real HTTP model provider (the mock AI service speaking the
OpenAI wire format), with scripted answers: a smuggled write, a fabricated column, a
fabricated table. AC4 and AC6 hold whatever the model says."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration


@pytest.fixture
def settings_overrides(mock_ai: str) -> dict[str, Any]:
    return {
        "ai_provider": "openai",
        "ai_base_url": f"{mock_ai}/v1",
        "ai_api_key": "sk-test-only-provider-key",
        "ai_model": "dbpilot-mock-sql",
    }


async def _script(stack: Stack, *answers: dict[str, Any] | str) -> None:
    async with httpx.AsyncClient() as http:
        texts = [a if isinstance(a, str) else json.dumps(a) for a in answers]
        await http.post(f"{stack.mock_ai_url}/_mock/script", json={"answers": texts})


async def _ask(stack: Stack, connection_id: str, question: str = "anything") -> dict[str, Any]:
    return dict(
        await stack.ok(
            "POST", stack.p("/ai/sql"), {"connection_id": connection_id, "question": question}
        )
    )


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        ({"sql": "SELECT 1; DELETE FROM orders", "explanation": "x"}, "multiple_statements"),
        (
            {
                "sql": "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
                "explanation": "x",
            },
            "write_or_admin",
        ),
        ("DELETE FROM orders WHERE status = 'cancelled'", "not_a_select"),
        ({"sql": "SELECT * FROM orders FOR UPDATE", "explanation": "x"}, "write_or_admin"),
        ({"sql": "SELECT pg_sleep(30)", "explanation": "x"}, "denied_function"),
        (
            {"sql": "SELECT /*+ Set(statement_timeout 0) */ * FROM orders", "explanation": "x"},
            "hidden_code",
        ),
        ({"sql": "SELECT " + "(" * 300 + "1" + ")" * 300, "explanation": "x"}, "too_deep"),
    ],
)
async def test_ac4_whatever_the_model_answers_only_one_read_only_query_is_accepted(
    ready: Stack, sources: Sources, answer: dict[str, Any] | str, code: str
) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    await _script(ready, answer)
    g = await _ask(ready, conn["id"])
    assert g["status"] == "refused" and g["guard"]["code"] == code
    assert g["provider"] == "openai" and g["executable"] is False
    resp = await ready.client.post(ready.p(f"/ai/generations/{g['id']}/run"))
    assert resp.status_code == 409


async def test_ac4_a_statement_lifted_out_of_a_refused_answer_still_cannot_run(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    await _script(
        ready,
        {
            "sql": "UPDATE orders SET status = 'cancelled' WHERE id = 7; SELECT * FROM orders",
            "explanation": "x",
        },
    )
    g = await _ask(ready, conn["id"])
    assert g["status"] == "refused" and g["guard"]["code"] == "multiple_statements"
    lifted = await ready.client.post(
        ready.p("/writes"),
        json={
            "connection_id": conn["id"],
            "sql": "update orders set status='cancelled' where id = 7",
        },
    )
    assert lifted.status_code == 422 and lifted.json()["error"]["code"] == "ai_generated_statement"
    # A statement the person wrote is theirs to confirm.
    own = await ready.client.post(
        ready.p("/writes"),
        json={
            "connection_id": conn["id"],
            "sql": "UPDATE orders SET status = 'cancelled' WHERE id = 8",
        },
    )
    assert own.status_code == 201, own.text


async def test_ac6_a_fabricated_column_or_table_is_refused(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    await _script(
        ready,
        {"sql": "SELECT name, loyalty_points FROM customers", "explanation": "x"},
        {"sql": "SELECT count(*) FROM invoices_2027", "explanation": "x"},
        {
            "sql": "SELECT c.name, o.discount_code FROM customers c JOIN orders o ON o.customer_id = c.id",
            "explanation": "x",
        },
        {
            "sql": "WITH t AS (SELECT customer_id, sum(total_amount) AS spent FROM orders GROUP BY customer_id) SELECT c.name, t.spent FROM customers c JOIN t ON t.customer_id = c.id ORDER BY t.spent DESC",
            "explanation": "x",
        },
        {"sql": "SELECT name, loyalty_points AS loyalty_points FROM customers", "explanation": "x"},
    )
    column = await _ask(ready, conn["id"])
    assert column["status"] == "refused" and column["guard"]["code"] == "unknown_identifier"
    assert column["unknown_columns"] == ["loyalty_points"] and column["executable"] is False
    table = await _ask(ready, conn["id"])
    assert table["status"] == "refused" and table["unknown_tables"] == ["invoices_2027"]
    qualified = await _ask(ready, conn["id"])
    assert qualified["unknown_columns"] == ["o.discount_code"]
    # Real names, an alias of a CTE and its output column: accepted.
    fine = await _ask(ready, conn["id"])
    assert fine["status"] == "accepted", fine
    # An alias is no evidence that the column it names exists.
    self_alias = await _ask(ready, conn["id"])
    assert self_alias["status"] == "refused" and self_alias["unknown_columns"] == ["loyalty_points"]


async def test_the_provider_key_is_sent_and_never_shown(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    g = await _ask(ready, conn["id"], "how many customers")
    assert g["status"] == "accepted"
    async with httpx.AsyncClient() as http:
        auth = (await http.get(f"{ready.mock_ai_url}/_mock/auth")).json()
    assert auth["openai"] == "Bearer sk-test-only-provider-key"
    assert "sk-test-only-provider-key" not in json.dumps(await ready.audit())


async def test_a_provider_outage_is_a_clear_502_and_is_recorded(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    async with httpx.AsyncClient() as http:
        await http.post(f"{ready.mock_ai_url}/_mock/fail", params={"status": 401, "times": 1})
    resp = await ready.client.post(
        ready.p("/ai/sql"), json={"connection_id": conn["id"], "question": "x"}
    )
    assert resp.status_code == 502 and resp.json()["error"]["code"] == "ai_auth"
    failed = await ready.ok("GET", ready.p("/ai/generations"), params={"status": "failed"})
    assert failed["items"] and failed["items"][0]["status"] == "failed"
