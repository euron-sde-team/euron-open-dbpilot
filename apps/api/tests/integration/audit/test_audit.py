"""AC10: every query, AI generation (accepted or refused), confirmed write, backup, restore
and migration writes an audit entry through one shared path, and nothing reaches a
connected database without one. The log is append-only in Postgres itself."""

from __future__ import annotations

import re

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration

_CONNECTOR = re.compile(r"^dbpilot_connector_calls_total\{[^}]*\} ([0-9.e+]+)$", re.M)


async def _connector_calls(stack: Stack) -> float:
    async with httpx.AsyncClient() as http:
        text = (await http.get(f"{stack.api_url}/metrics")).text
    return sum(float(v) for v in _CONNECTOR.findall(text))


async def test_ac10_every_connector_call_has_exactly_one_audit_entry(
    ready: Stack, sources: Sources
) -> None:
    """The gateway is the only door: across a mixed workload, the number of operations that
    reached a database equals the number of gateway audit entries written for them."""
    before_calls = await _connector_calls(ready)
    engine = create_async_engine(ready.settings.database_url)
    async with engine.connect() as c:
        before_id = (
            await c.execute(sa.text("SELECT coalesce(max(id), 0) FROM audit_log"))
        ).scalar_one()
    conn = await ready.connection(sources.pg(admin=True))
    await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
    await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    await ready.ok("GET", ready.p(f"/connections/{conn['id']}/health"))
    await ready.sql(conn["id"], "SELECT count(*) FROM orders")
    await ready.sql(conn["id"], "DELETE FROM orders")  # refused: no connector call
    good = await ready.ok(
        "POST", ready.p("/ai/sql"), {"connection_id": conn["id"], "question": "how many customers"}
    )
    await ready.ok("POST", ready.p(f"/ai/generations/{good['id']}/run"))
    await ready.ok(
        "POST",
        ready.p("/ai/sql"),
        {"connection_id": conn["id"], "question": "Delete all cancelled orders"},
    )
    prepared = await ready.ok(
        "POST",
        ready.p("/writes"),
        {"connection_id": conn["id"], "sql": "INSERT INTO notes (body) VALUES ('audited')"},
        status=201,
    )
    await ready.confirm(prepared)
    await ready.ok("POST", ready.p("/backups"), {"connection_id": conn["id"]}, status=201)
    after_calls = await _connector_calls(ready)
    async with engine.connect() as c:
        rows = (
            await c.execute(
                sa.text(
                    "SELECT action, details FROM audit_log WHERE id > :i AND project_id = :p ORDER BY id"
                ),
                {"i": before_id, "p": ready.project_id},
            )
        ).all()
    await engine.dispose()
    gateway_entries = [a for a, d in rows if d.get("mode") in ("read", "write")]
    assert len(gateway_entries) == after_calls - before_calls
    actions = [a for a, _ in rows]
    for expected in (
        "connection.created",
        "connection.tested",
        "schema.discovered",
        "connection.health_checked",
        "query.run",
        "query.refused",
        "ai.generated",
        "ai.refused",
        "write.requested",
        "write.confirmed",
        "write.executed",
        "backup.created",
        "backup.completed",
    ):
        assert expected in actions, expected


async def test_ac10_the_audit_log_is_append_only_in_the_database(ready: Stack) -> None:
    engine = create_async_engine(ready.settings.database_url)
    for statement in (
        "UPDATE audit_log SET action = 'forged'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        async with engine.begin() as c:
            with pytest.raises(Exception, match="append-only"):
                await c.execute(sa.text(statement))
    await engine.dispose()


async def test_audit_is_filtered_paginated_and_admin_only(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    for _ in range(3):
        await ready.sql(conn["id"], "SELECT 1")
    page = await ready.ok("GET", ready.p("/audit"), params={"action": "query.", "limit": 2})
    assert len(page["items"]) == 2 and page["next_cursor"]
    rest = await ready.ok(
        "GET",
        ready.p("/audit"),
        params={"action": "query.", "limit": 2, "cursor": page["next_cursor"]},
    )
    assert rest["items"][0]["id"] < page["items"][-1]["id"]
    org = await ready.ok("GET", f"/api/v1/orgs/{ready.org_id}/audit", params={"action": "query."})
    assert org["items"]
    viewer, _ = await ready.member("viewer")
    assert (await viewer.get(ready.p("/audit"))).status_code == 403
