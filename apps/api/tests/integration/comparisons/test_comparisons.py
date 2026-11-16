"""AC8: comparing two schemas produces a diff and a migration script; DBPilot never runs the
script itself, only a person through the confirmed-write path, after which the schemas
match."""

from __future__ import annotations

import pytest

from dbpilot.demo import sql_sources
from tests.integration.conftest import (
    READER_PASSWORD,
    TODAY,
    Sources,
    Stack,
    admin_url,
    recreate_pg_database,
    source_engine,
    source_scalar,
)

pytestmark = pytest.mark.integration


def _prepare_pair() -> None:
    """Two copies of the shop; the "to" one gets a new column, index, table and key."""
    for name in ("compare_from_test", "compare_to_test"):
        recreate_pg_database(name)
        sql_sources.build_shop(admin_url("postgresql", name), READER_PASSWORD, TODAY)
    engine = source_engine("postgresql", "compare_to_test")
    with engine.begin() as c:
        c.exec_driver_sql("ALTER TABLE customers ADD COLUMN loyalty_tier varchar(20)")
        c.exec_driver_sql("ALTER TABLE customers ALTER COLUMN country TYPE varchar(80)")
        c.exec_driver_sql("CREATE INDEX ix_order_items_product ON order_items (product_id)")
        c.exec_driver_sql(
            "CREATE TABLE coupons (id serial PRIMARY KEY, code varchar(30) NOT NULL, "
            "customer_id integer REFERENCES customers(id))"
        )
        c.exec_driver_sql("DROP TABLE notes")
    engine.dispose()


async def test_ac8_the_script_is_generated_never_run_and_runs_only_when_confirmed(
    ready: Stack, sources: Sources
) -> None:
    _prepare_pair()
    src = await ready.connection(sources.pg(admin=True, database="compare_from_test", name="From"))
    dst = await ready.connection(sources.pg(admin=True, database="compare_to_test", name="To"))
    cmp = await ready.ok(
        "POST",
        ready.p("/comparisons"),
        {"from_connection_id": src["id"], "to_connection_id": dst["id"]},
        status=201,
    )
    assert cmp["identical"] is False and cmp["runnable"] is True
    diff = cmp["diff"]
    assert diff["added_tables"] == ["public.coupons"] and diff["removed_tables"] == ["public.notes"]
    changed = {d["table"]: d for d in diff["changed_tables"]}
    assert changed["public.customers"]["added_columns"] == ["loyalty_tier"]
    assert changed["public.customers"]["changed_columns"][0]["name"] == "country"
    assert changed["public.order_items"]["added_indexes"] == ["ix_order_items_product"]
    assert cmp["destructive"] is True  # DROP TABLE notes is in it, flagged
    assert any(s["destructive"] and s["sql"].startswith("DROP TABLE") for s in cmp["statements"])
    # DBPilot ran nothing: the "from" database is untouched.
    assert (
        source_scalar(
            "postgresql",
            "compare_from_test",
            "SELECT count(*) FROM information_schema.columns WHERE column_name = 'loyalty_tier'",
        )
        == 0
    )
    assert not await ready.audit(action="migration.", connection_id=src["id"])

    prepared = await ready.ok("POST", ready.p(f"/comparisons/{cmp['id']}/run"), status=201)
    assert prepared["kind"] == "migration" and prepared["comparison_id"] == cmp["id"]
    assert [s["sql"] for s in prepared["statements"]] == [s["sql"] for s in cmp["statements"]]
    assert prepared["connection_id"] == src["id"]
    assert (
        source_scalar(
            "postgresql",
            "compare_from_test",
            "SELECT count(*) FROM pg_tables WHERE tablename = 'coupons'",
        )
        == 0
    )
    result = await ready.confirm(prepared)
    assert result["confirmation"]["status"] == "executed", result
    again = await ready.ok(
        "POST",
        ready.p("/comparisons"),
        {"from_connection_id": src["id"], "to_connection_id": dst["id"]},
        status=201,
    )
    assert again["identical"] is True and again["statements"] == []
    actions = [e["action"] for e in await ready.audit(action="migration.", connection_id=src["id"])]
    assert {"migration.requested", "migration.confirmed", "migration.executed"} <= set(actions)


async def test_two_points_in_time_on_one_connection(ready: Stack, sources: Sources) -> None:
    recreate_pg_database("compare_from_test")
    sql_sources.build_shop(admin_url("postgresql", "compare_from_test"), READER_PASSWORD, TODAY)
    conn = await ready.connection(sources.pg(admin=True, database="compare_from_test"))
    before = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/schema/refresh"))
    engine = source_engine("postgresql", "compare_from_test")
    with engine.begin() as c:
        c.exec_driver_sql("ALTER TABLE products ADD COLUMN sku varchar(40)")
    engine.dispose()
    after = await ready.ok("POST", ready.p(f"/connections/{conn['id']}/schema/refresh"))
    cmp = await ready.ok(
        "POST",
        ready.p("/comparisons"),
        {"from_snapshot_id": before["id"], "to_snapshot_id": after["id"]},
        status=201,
    )
    assert [s["sql"] for s in cmp["statements"]] == [
        'ALTER TABLE "public"."products" ADD COLUMN "sku" VARCHAR(40)'
    ]
    listed = await ready.ok("GET", ready.p("/comparisons"))
    assert listed["items"][0]["id"] == cmp["id"]
    snapshots = await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema/snapshots"))
    assert [s["id"] for s in snapshots["items"][:2]] == [after["id"], before["id"]]


async def test_comparison_needs_the_same_sql_engine(ready: Stack, sources: Sources) -> None:
    pg = await ready.connection(sources.pg())
    my = await ready.connection(sources.mysql())
    resp = await ready.client.post(
        ready.p("/comparisons"), json={"from_connection_id": pg["id"], "to_connection_id": my["id"]}
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "engine_mismatch"
