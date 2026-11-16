"""AC12 (aggregates): the dashboards are counted live from DBPilot's own Postgres on a cache
miss, behind a short response cache; no worker maintains them."""

from __future__ import annotations

import pytest

from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration


async def test_ac12_dashboards_are_live_counts_behind_a_short_cache(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    await ready.sql(conn["id"], "SELECT 1")
    await ready.sql(conn["id"], "DELETE FROM orders")  # refused
    first = await ready.ok("GET", f"/api/v1/orgs/{ready.org_id}/dashboard")
    assert first["cached"] is False and first["cache_ttl_s"] == 15
    totals = first["totals"]
    assert totals["projects"] == 1 and totals["connections"] == 1
    assert totals["queries_total"] == 2 and totals["queries_refused_7d"] == 1
    await ready.sql(conn["id"], "SELECT 2")
    cached = await ready.ok("GET", f"/api/v1/orgs/{ready.org_id}/dashboard")
    assert cached["cached"] is True and cached["totals"]["queries_total"] == 2  # within the TTL
    project = await ready.ok("GET", ready.p("/overview"))
    assert project["scope"] == "project" and project["totals"]["queries_total"] == 3
