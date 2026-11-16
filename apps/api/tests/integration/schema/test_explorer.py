"""The explorer: AC6 (the schema is a live discovery), the snapshot cache, search, the ER
view and row previews (which are normal audited reads)."""

from __future__ import annotations

import pytest

from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration


async def test_ac6_schema_is_discovered_from_the_live_database(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    schema = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    assert schema["cached"] is False and schema["engine"] == "postgresql"
    objects = {o["name"]: o for o in schema["objects"]}
    assert set(objects) == {
        "customers",
        "products",
        "orders",
        "order_items",
        "notes",
        "monthly_revenue",
    }
    orders = objects["orders"]
    assert orders["row_count"] == 2500 and orders["kind"] == "table"
    assert {c["name"] for c in orders["columns"]} == {
        "id",
        "customer_id",
        "status",
        "order_date",
        "total_amount",
    }
    assert next(c for c in orders["columns"] if c["name"] == "id")["primary_key"] is True
    assert orders["foreign_keys"][0]["ref_table"] == "public.customers"
    assert {i["name"] for i in orders["indexes"]} >= {"ix_orders_order_date", "ix_orders_customer"}
    assert objects["monthly_revenue"]["kind"] == "view"
    again = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    assert again["cached"] is True and again["id"] == schema["id"]
    refreshed = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/schema/refresh"))
    assert refreshed["id"] != schema["id"] and refreshed["trigger"] == "refresh"
    assert (await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema")))["id"] == refreshed[
        "id"
    ]


async def test_search_relationships_and_previews(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg(max_rows=20))
    hits = await ready.ok(
        "GET", ready.p(f"/connections/{conn['id']}/schema/search"), params={"q": "customer"}
    )
    kinds = {(h["object"], h["match_kind"]) for h in hits}
    assert ("public.customers", "object") in kinds and ("public.orders", "column") in kinds
    er = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema/relationships"))
    edges = {(e["source"], e["target"]) for e in er["edges"]}
    assert ("public.orders", "public.customers") in edges and (
        "public.order_items",
        "public.products",
    ) in edges
    preview = await ready.ok(
        "POST",
        ready.p(f"/connections/{conn['id']}/preview"),
        {"object": "public.customers", "limit": 5},
    )
    assert (
        preview["source"] == "preview"
        and preview["status"] == "succeeded"
        and preview["row_count"] == 5
    )
    assert preview["statement"] == 'SELECT * FROM "public"."customers" LIMIT 5'
    missing = await ready.client.post(
        ready.p(f"/connections/{conn['id']}/preview"), json={"object": "pg_shadow"}
    )
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "unknown_object"


async def test_mysql_and_sqlite_explorers(ready: Stack, sources: Sources) -> None:
    my = await ready.connection(sources.mysql())
    schema = await ready.ok("GET", ready.p(f"/connections/{my['id']}/schema"))
    names = {o["name"] for o in schema["objects"]}
    assert names >= {"departments", "employees", "leave_requests"}
    assert all(o["schema_name"] is None for o in schema["objects"])
    lite = await ready.sqlite(sources.sqlite_path)
    s2 = await ready.ok("GET", ready.p(f"/connections/{lite['id']}/schema"))
    assert {o["name"] for o in s2["objects"]} == {"plans", "accounts", "invoices"}
    er = await ready.ok("GET", ready.p(f"/connections/{lite['id']}/schema/relationships"))
    assert ("invoices", "accounts") in {(e["source"], e["target"]) for e in er["edges"]}
    prev = await ready.ok(
        "POST", ready.p(f"/connections/{lite['id']}/preview"), {"object": "plans"}
    )
    assert prev["row_count"] == 4


async def test_saved_queries(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    saved = await ready.ok(
        "POST",
        ready.p("/saved-queries"),
        {
            "connection_id": conn["id"],
            "name": "Revenue by month",
            "statement": "SELECT * FROM monthly_revenue",
        },
        status=201,
    )
    assert (await ready.ok("GET", ready.p("/saved-queries")))[0]["id"] == saved["id"]
    changed = await ready.ok(
        "PATCH", ready.p(f"/saved-queries/{saved['id']}"), {"name": "Monthly revenue"}
    )
    assert changed["name"] == "Monthly revenue"
    await ready.ok("DELETE", ready.p(f"/saved-queries/{saved['id']}"), status=204)
    assert await ready.ok("GET", ready.p("/saved-queries")) == []
