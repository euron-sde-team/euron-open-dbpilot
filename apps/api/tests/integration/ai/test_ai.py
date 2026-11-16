"""The AI assistant with the built-in deterministic model: AC4 (an AI-generated statement
that is not one read-only query never runs, with no override) and AC6 (its context is the
live discovery, the same one the explorer shows)."""

from __future__ import annotations

import pytest

from tests.integration.conftest import Sources, Stack, source_scalar

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("question", "verb"),
    [
        ("Delete all cancelled orders", "DELETE"),
        ("drop the customers table", "DROP"),
        ("set the product prices", "UPDATE"),
    ],
)
async def test_ac4_an_ai_generated_write_is_refused_and_can_never_run(
    ready: Stack, sources: Sources, question: str, verb: str
) -> None:
    conn = await ready.connection(sources.pg(admin=True))  # this login could write
    counts = source_scalar("postgresql", "shop_test", "SELECT count(*) FROM orders")
    g = await ready.ok(
        "POST", ready.p("/ai/sql"), {"connection_id": conn["id"], "question": question}
    )
    assert g["status"] == "refused" and g["sql"].startswith(verb)
    assert g["executable"] is False and g["guard"]["passed"] is False
    assert g["guard"]["code"] == "not_a_select"
    # There is no way to run it: the run route refuses, and nothing takes an override.
    resp = await ready.client.post(ready.p(f"/ai/generations/{g['id']}/run"), json={"page_size": 5})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "ai_statement_refused"
    forced = await ready.client.post(
        ready.p(f"/ai/generations/{g['id']}/run"),
        json={"page_size": 5, "force": True, "override": True},
    )
    assert forced.status_code == 409
    # Not through the confirmed-write path either: the statement is recognized as the AI's.
    write = await ready.client.post(
        ready.p("/writes"), json={"connection_id": conn["id"], "sql": g["sql"]}
    )
    assert write.status_code == 422 and write.json()["error"]["code"] == "ai_generated_statement"
    sloppy = "  " + g["sql"].lower().replace(" ", "  ") + " ;"
    write2 = await ready.client.post(
        ready.p("/writes"), json={"connection_id": conn["id"], "sql": sloppy}
    )
    assert write2.status_code == 422 and write2.json()["error"]["code"] == "ai_generated_statement"
    assert source_scalar("postgresql", "shop_test", "SELECT count(*) FROM orders") == counts
    actions = [e["action"] for e in await ready.audit(connection_id=conn["id"])]
    assert "ai.refused" in actions and "ai.run_refused" in actions and "write.refused" in actions
    listed = await ready.ok("GET", ready.p("/ai/generations"), params={"status": "refused"})
    assert listed["items"][0]["id"] == g["id"]  # refused generations are kept


async def test_ac4_an_accepted_generation_runs_through_the_normal_read_path(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    g = await ready.ok(
        "POST",
        ready.p("/ai/sql"),
        {"connection_id": conn["id"], "question": "how many orders per status"},
    )
    assert g["status"] == "accepted" and g["executable"] is True
    assert g["tables"] == ["public.orders"]
    out = await ready.ok("POST", ready.p(f"/ai/generations/{g['id']}/run"), {"page_size": 10})
    assert out["status"] == "succeeded" and out["source"] == "ai"
    assert out["ai_generation_id"] == g["id"] and out["row_count"] == 5
    entry = (await ready.audit(action="query.run", connection_id=conn["id"]))[0]
    assert entry["details"]["ai_generation_id"] == g["id"]


async def test_ac6_the_ai_context_is_the_live_discovery_the_explorer_shows(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    schema = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    g = await ready.ok(
        "POST",
        ready.p("/ai/sql"),
        {"connection_id": conn["id"], "question": "total revenue by country"},
    )
    assert g["schema_snapshot_id"] == schema["id"]
    assert g["status"] == "accepted"
    real = {f"{o['schema_name']}.{o['name']}" for o in schema["objects"]}
    assert set(g["tables"]) <= real
    # The prompt the model got names only discovered tables (stored with the generation).
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(ready.settings.database_url)
    async with engine.connect() as c:
        context = (
            await c.execute(
                text("SELECT schema_context FROM ai_generations WHERE id = :i"), {"i": g["id"]}
            )
        ).scalar_one()
    await engine.dispose()
    block = context.split("Tables:", 1)[1]
    named = {line.split()[1] for line in block.splitlines() if line.startswith("- ")}
    assert named == real


async def test_ai_is_sql_only_and_explain_never_runs(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    out = await ready.ok(
        "POST",
        ready.p("/ai/explain"),
        {"connection_id": conn["id"], "sql": "DELETE FROM orders WHERE id = 1"},
    )
    assert out["executed"] is False and out["analysis"]["read_only"] is False
    assert "deletes rows from orders" in out["explanation"]
    syntax = await ready.ok(
        "POST",
        ready.p("/ai/explain"),
        {"dialect": "mysql", "sql": "select count(*) from employees", "use_model": False},
    )
    assert syntax["source"] == "syntax"
    assert not await ready.audit(action="query.", connection_id=conn["id"])


async def test_index_suggestions_and_diagnosis_use_the_real_schema(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    await ready.sql(conn["id"], "SELECT count(*) FROM order_items WHERE product_id = 3")
    out = await ready.ok("POST", ready.p("/ai/index-suggestions"), {"connection_id": conn["id"]})
    fk = [s for s in out["suggestions"] if s["source"] == "foreign_key"]
    assert any(s["table"] == "public.order_items" and s["columns"] == ["product_id"] for s in fk)
    assert fk[0]["ddl"].startswith("CREATE INDEX")
    diag = await ready.ok(
        "POST",
        ready.p("/ai/diagnose"),
        {
            "connection_id": conn["id"],
            "sql": "SELECT totl_amount FROM orders",
            "error": 'column "totl_amount" does not exist',
        },
    )
    assert diag["category"] == "unknown_column" and "total_amount" in diag["similar"]
