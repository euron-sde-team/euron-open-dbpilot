"""AC7: a DBPilot backup restores into a fresh database with the same schema and row counts,
and a restore needs its own confirmation and audit entries, exactly like a write."""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.conftest import (
    Sources,
    Stack,
    recreate_mysql_database,
    recreate_pg_database,
    source_engine,
    source_scalar,
)

pytestmark = pytest.mark.integration

SHOP_TABLES = ("customers", "products", "orders", "order_items", "notes")


def _shape(objects: list[dict[str, Any]]) -> dict[str, Any]:
    """What must survive a restore: tables, columns (name, type, nullability), keys,
    indexes and row counts."""
    out: dict[str, Any] = {}
    for o in objects:
        out[o["name"]] = {
            "kind": o["kind"],
            "columns": [
                (c["name"], c["type"], c["nullable"], c["primary_key"]) for c in o["columns"]
            ],
            "fks": sorted(
                (tuple(f["columns"]), f["ref_table"].split(".")[-1], tuple(f["ref_columns"]))
                for f in o["foreign_keys"]
            ),
            "indexes": sorted((tuple(i["columns"]), i["unique"]) for i in o["indexes"]),
        }
    return out


async def _backup(stack: Stack, connection_id: str) -> dict[str, Any]:
    b = dict(
        await stack.ok("POST", stack.p("/backups"), {"connection_id": connection_id}, status=201)
    )
    assert b["status"] == "succeeded", b
    return b


async def test_ac7_postgres_backup_restores_into_a_fresh_database(
    ready: Stack, sources: Sources
) -> None:
    recreate_pg_database("restore_target_test")
    source = await ready.connection(sources.pg(admin=True))
    target = await ready.connection(
        sources.pg(admin=True, database="restore_target_test", name="Fresh")
    )
    source_schema = await ready.ok("GET", ready.p(f"/connections/{source['id']}/schema"))
    backup = await _backup(ready, source["id"])
    assert backup["integrity_ok"] is True and backup["checksum"] and backup["size_bytes"] > 0
    expected = {
        t: source_scalar("postgresql", "shop_test", f"SELECT count(*) FROM {t}")
        for t in SHOP_TABLES
    }
    assert {k.split(".")[-1]: v for k, v in backup["tables"].items()} == expected

    prepared = await ready.ok(
        "POST",
        ready.p(f"/backups/{backup['id']}/restore"),
        {"target_connection_id": target["id"]},
        status=201,
    )
    assert prepared["kind"] == "restore" and prepared["status"] == "pending"
    assert prepared["restore"]["total_rows"] == sum(expected.values())
    # Nothing is created until the second step.
    assert not source_scalar(
        "postgresql",
        "restore_target_test",
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'",
    )
    result = await ready.confirm(prepared)
    assert result["confirmation"]["status"] == "executed", result
    assert result["confirmation"]["result"]["verified"] is True
    restored = {
        t: source_scalar("postgresql", "restore_target_test", f"SELECT count(*) FROM {t}")
        for t in SHOP_TABLES
    }
    assert restored == expected
    target_schema = await ready.ok("POST", ready.p(f"/connections/{target['id']}/schema/refresh"))
    assert _shape(target_schema["objects"]) == _shape(source_schema["objects"])
    # The sequences continue after the restored keys (new rows do not collide).
    engine = source_engine("postgresql", "restore_target_test")
    with engine.begin() as c:
        new_id = c.exec_driver_sql(
            "INSERT INTO customers (name, email, country, created_at) VALUES ('N', 'n@example.com', 'X', now()) RETURNING id"
        ).scalar()
    engine.dispose()
    assert new_id == expected["customers"] + 1
    actions = [e["action"] for e in await ready.audit(action="restore.")]
    assert actions.index("restore.executed") < actions.index("restore.confirmed")  # newest first
    assert "restore.requested" in actions


async def test_ac7_a_restore_into_a_database_that_has_the_tables_is_refused(
    ready: Stack, sources: Sources
) -> None:
    source = await ready.connection(sources.pg(admin=True))
    backup = await _backup(ready, source["id"])
    resp = await ready.client.post(
        ready.p(f"/backups/{backup['id']}/restore"), json={"target_connection_id": source["id"]}
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "restore_target_not_empty"
    assert "public.orders" in resp.json()["error"]["details"]["tables"]


async def test_ac7_mysql_backup_restores_into_a_fresh_database(
    ready: Stack, sources: Sources
) -> None:
    recreate_mysql_database("people_restore_test")
    source = await ready.connection(sources.mysql(admin=True))
    target = await ready.connection(
        sources.mysql(admin=True, database="people_restore_test", name="Fresh people")
    )
    backup = await _backup(ready, source["id"])
    prepared = await ready.ok(
        "POST",
        ready.p(f"/backups/{backup['id']}/restore"),
        {"target_connection_id": target["id"]},
        status=201,
    )
    result = await ready.confirm(prepared)
    assert result["confirmation"]["result"]["verified"] is True, result
    for table in ("departments", "employees", "leave_requests"):
        assert source_scalar(
            "mysql", "people_restore_test", f"SELECT count(*) FROM {table}"
        ) == source_scalar("mysql", "people_test", f"SELECT count(*) FROM {table}")
    fks = source_scalar(
        "mysql",
        "people_restore_test",
        "SELECT count(*) FROM information_schema.referential_constraints WHERE constraint_schema = 'people_restore_test'",
    )
    assert fks == 2


async def test_ac7_sqlite_backup_restores_into_a_new_sqlite_connection(
    ready: Stack, sources: Sources
) -> None:
    source = await ready.sqlite(sources.sqlite_path)
    backup = await _backup(ready, source["id"])
    prepared = await ready.ok(
        "POST",
        ready.p(f"/backups/{backup['id']}/restore"),
        {"new_sqlite_name": "Restored subscriptions"},
        status=201,
    )
    result = await ready.confirm(prepared)
    assert result["confirmation"]["result"]["verified"] is True, result
    new_id = result["confirmation"]["result"]["target_connection_id"]
    counts = await ready.sql(
        new_id, "SELECT (SELECT count(*) FROM plans) AS p, (SELECT count(*) FROM invoices) AS i"
    )
    original = await ready.sql(
        source["id"],
        "SELECT (SELECT count(*) FROM plans) AS p, (SELECT count(*) FROM invoices) AS i",
    )
    assert counts["page"]["rows"] == original["page"]["rows"]


async def test_backups_need_admins_and_viewers_only_see_granted_ones(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    hidden = await ready.connection(sources.mysql())
    await _backup(ready, conn["id"])
    await _backup(ready, hidden["id"])
    viewer, viewer_id = await ready.member("viewer")
    await ready.ok("PUT", ready.p(f"/connections/{conn['id']}/grants"), {"user_id": viewer_id})
    denied = await viewer.post(ready.p("/backups"), json={"connection_id": conn["id"]})
    assert denied.status_code == 403
    listed = (await viewer.get(ready.p("/backups"))).json()["items"]
    assert {b["connection_id"] for b in listed} == {conn["id"]}


async def test_verify_and_delete(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    backup = await _backup(ready, conn["id"])
    checked = await ready.ok("POST", ready.p(f"/backups/{backup['id']}/verify"))
    assert checked["integrity_ok"] is True and "read back" in checked["integrity_detail"]
    download = await ready.client.get(ready.p(f"/backups/{backup['id']}/download"))
    assert download.status_code == 200 and download.content[:2] == b"\x1f\x8b"  # gzip
    await ready.ok("DELETE", ready.p(f"/backups/{backup['id']}"), status=204)
    assert (await ready.client.get(ready.p(f"/backups/{backup['id']}"))).status_code == 404
    actions = {e["action"] for e in await ready.audit(action="backup.")}
    assert {
        "backup.created",
        "backup.completed",
        "backup.verified",
        "backup.downloaded",
        "backup.deleted",
    } <= actions


async def test_mongodb_and_redis_backups_are_honestly_refused(ready: Stack) -> None:
    conn = await ready.connection(
        {"name": "R", "kind": "redis", "host": "127.0.0.1", "port": 1, "database": "0"}
    )
    resp = await ready.client.post(ready.p("/backups"), json={"connection_id": conn["id"]})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "engine_not_supported"


HOSTILE_SCHEMA = 'x"; DROP TABLE canary; --'
HOSTILE_TABLE = "t's.v1"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def test_ac7_names_from_the_source_are_never_run_as_sql_by_a_restore(
    ready: Stack, sources: Sources
) -> None:
    """Schema, table and column names come from the source database; a restore must recreate
    them, quoted, and never execute them (a schema named to close the quote and DROP a
    table on the target, a table and a column with quotes and a dot in their names)."""
    recreate_pg_database("hostile_source_test")
    recreate_pg_database("hostile_target_test")
    table = f"{_q(HOSTILE_SCHEMA)}.{_q(HOSTILE_TABLE)}"
    engine = source_engine("postgresql", "hostile_source_test")
    with engine.begin() as c:
        c.exec_driver_sql(f"CREATE SCHEMA {_q(HOSTILE_SCHEMA)}")
        c.exec_driver_sql(
            f"CREATE TABLE {table} (id serial PRIMARY KEY, {_q(chr(39) + 'it' + chr(39) + 's')} text)"
        )
        c.exec_driver_sql(
            f"INSERT INTO {table} ({_q(chr(39) + 'it' + chr(39) + 's')}) VALUES ('a'), ('b')"
        )
    engine.dispose()
    target_engine = source_engine("postgresql", "hostile_target_test")
    with target_engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE canary (id int)")
        c.exec_driver_sql("INSERT INTO canary VALUES (1)")
    source = await ready.connection(
        sources.pg(admin=True, database="hostile_source_test", name="Hostile")
    )
    target = await ready.connection(
        sources.pg(admin=True, database="hostile_target_test", name="Target")
    )
    backup = await _backup(ready, source["id"])
    assert sum(backup["tables"].values()) == 2
    prepared = await ready.ok(
        "POST",
        ready.p(f"/backups/{backup['id']}/restore"),
        {"target_connection_id": target["id"]},
        status=201,
    )
    result = await ready.confirm(prepared)
    assert result["confirmation"]["status"] == "executed", result
    assert result["confirmation"]["result"]["verified"] is True
    # The canary is untouched, the hostile table is restored under its own name.
    assert source_scalar("postgresql", "hostile_target_test", "SELECT count(*) FROM canary") == 1
    assert source_scalar("postgresql", "hostile_target_test", f"SELECT count(*) FROM {table}") == 2
    with target_engine.begin() as c:
        new_id = c.exec_driver_sql(f"INSERT INTO {table} DEFAULT VALUES RETURNING id").scalar()
    target_engine.dispose()
    assert new_id == 3  # the serial was moved past the restored rows, by its real name
