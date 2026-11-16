"""SQLite: queries on a private read-only copy, AI, and confirmed writes that replace the
stored file only when they succeed (copy on write)."""

from __future__ import annotations

import pytest

from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration


async def test_sqlite_reads_ai_and_confirmed_writes(ready: Stack, sources: Sources) -> None:
    conn = await ready.sqlite(sources.sqlite_path)
    count = await ready.sql(conn["id"], "SELECT count(*) AS n FROM plans")
    assert count["page"]["rows"] == [[4]] and count["estimated_scan_rows"] is not None
    refused = await ready.sql(conn["id"], "DELETE FROM plans")
    assert refused["status"] == "refused"
    gen = await ready.ok(
        "POST",
        ready.p("/ai/sql"),
        {"connection_id": conn["id"], "question": "how many accounts per status"},
    )
    assert gen["status"] == "accepted"
    ran = await ready.ok("POST", ready.p(f"/ai/generations/{gen['id']}/run"))
    assert ran["status"] == "succeeded"
    original_key = conn["file_size"]
    prepared = await ready.ok(
        "POST",
        ready.p("/writes"),
        {
            "connection_id": conn["id"],
            "sql": "INSERT INTO plans (id, name, monthly_price) VALUES (5, 'Scale', 1990)",
        },
        status=201,
    )
    assert any("SQLite writes go to a copy" in w for w in prepared["warnings"])
    result = await ready.confirm(prepared)
    assert result["confirmation"]["status"] == "executed"
    after = await ready.sql(conn["id"], "SELECT count(*) FROM plans")
    assert after["page"]["rows"] == [[5]]
    shown = await ready.ok("GET", ready.p(f"/connections/{conn['id']}"))
    assert shown["file_size"] >= original_key
    failing = await ready.ok(
        "POST",
        ready.p("/writes"),
        {
            "connection_id": conn["id"],
            "sql": "INSERT INTO plans (id, name, monthly_price) VALUES (6, 'X', 1);\nINSERT INTO plans (id) VALUES (7)",
        },
        status=201,
    )
    out = await ready.confirm(failing)
    assert out["confirmation"]["status"] == "failed"
    still = await ready.sql(conn["id"], "SELECT count(*) FROM plans")
    assert still["page"]["rows"] == [[5]]  # the failed write never replaced the file
    attach = await ready.client.post(
        ready.p("/writes"),
        json={"connection_id": conn["id"], "sql": "ATTACH DATABASE '/tmp/x.db' AS x"},
    )
    assert attach.status_code == 422
