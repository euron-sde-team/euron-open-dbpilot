"""AC12 (aggregates) with the response cache off: every read is a live count."""

from __future__ import annotations

from typing import Any

import pytest

from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration


@pytest.fixture
def settings_overrides() -> dict[str, Any]:
    return {"dashboard_cache_ttl_s": 0}


async def test_without_a_cache_every_read_is_live(ready: Stack, sources: Sources) -> None:
    conn = await ready.connection(sources.pg())
    for expected in (1, 2, 3):
        await ready.sql(conn["id"], "SELECT 1")
        out = await ready.ok("GET", ready.p("/overview"))
        assert out["cached"] is False and out["totals"]["queries_total"] == expected
    prepared = await ready.ok(
        "POST",
        ready.p("/writes"),
        {
            "connection_id": (await ready.connection(sources.pg(admin=True)))["id"],
            "sql": "INSERT INTO notes (body) VALUES ('x')",
        },
        status=201,
    )
    assert (await ready.ok("GET", ready.p("/overview")))["totals"]["pending_writes"] == 1
    await ready.confirm(prepared)
    after = (await ready.ok("GET", ready.p("/overview")))["totals"]
    assert after["pending_writes"] == 0 and after["writes_executed_7d"] == 1
