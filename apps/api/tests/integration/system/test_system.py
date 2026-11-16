"""Health, readiness, capabilities, the system view, and Prometheus metrics that move with
the real call sites (not a dependency added and forgotten)."""

from __future__ import annotations

import re

import httpx
import pytest

from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration


def _value(text: str, metric: str, **labels: str) -> float:
    total = 0.0
    for line in text.splitlines():
        if not line.startswith(metric + "{"):
            continue
        if all(f'{k}="{v}"' in line for k, v in labels.items()):
            total += float(line.rsplit(" ", 1)[1])
    return total


async def _metrics(stack: Stack) -> str:
    async with httpx.AsyncClient() as http:
        return (await http.get(f"{stack.api_url}/metrics")).text


async def test_metrics_move_with_every_domain_call_site(ready: Stack, sources: Sources) -> None:
    before = await _metrics(ready)
    conn = await ready.connection(sources.pg(admin=True))
    await ready.ok("POST", ready.p(f"/connections/{conn['id']}/test"))
    await ready.sql(conn["id"], "SELECT 1")
    await ready.sql(conn["id"], "DROP TABLE orders")
    await ready.ok(
        "POST", ready.p("/ai/sql"), {"connection_id": conn["id"], "question": "how many customers"}
    )
    await ready.ok(
        "POST",
        ready.p("/ai/sql"),
        {"connection_id": conn["id"], "question": "Delete all cancelled orders"},
    )
    prepared = await ready.ok(
        "POST",
        ready.p("/writes"),
        {"connection_id": conn["id"], "sql": "INSERT INTO notes (body) VALUES ('m')"},
        status=201,
    )
    await ready.confirm(prepared)
    await ready.ok("POST", ready.p("/backups"), {"connection_id": conn["id"]}, status=201)
    await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    await ready.ok("GET", ready.p(f"/connections/{conn['id']}/schema"))
    after = await _metrics(ready)
    expectations = [
        ("dbpilot_connection_tests_total", {"engine": "postgresql", "outcome": "ok"}),
        ("dbpilot_queries_total", {"engine": "postgresql", "mode": "read", "status": "succeeded"}),
        ("dbpilot_queries_total", {"status": "refused"}),
        ("dbpilot_queries_total", {"mode": "write", "status": "succeeded"}),
        ("dbpilot_guard_refusals_total", {"path": "editor", "code": "not_a_select"}),
        ("dbpilot_guard_refusals_total", {"path": "ai"}),
        ("dbpilot_ai_generations_total", {"kind": "sql", "outcome": "accepted"}),
        ("dbpilot_ai_generations_total", {"kind": "sql", "outcome": "refused"}),
        ("dbpilot_confirmations_total", {"kind": "write", "stage": "requested"}),
        ("dbpilot_confirmations_total", {"kind": "write", "stage": "confirmed"}),
        ("dbpilot_confirmations_total", {"kind": "write", "stage": "executed"}),
        (
            "dbpilot_backups_total",
            {"engine": "postgresql", "trigger": "manual", "status": "succeeded"},
        ),
        ("dbpilot_connector_calls_total", {"action": "query.run", "outcome": "ok"}),
        ("dbpilot_schema_discoveries_total", {"engine": "postgresql", "outcome": "ok"}),
        ("dbpilot_cache_requests_total", {"cache": "schema", "result": "hit"}),
        ("dbpilot_audit_entries_total", {"group": "write"}),
        ("dbpilot_http_requests_total", {"route": "/api/v1/projects/{project_id}/queries/sql"}),
    ]
    for metric, labels in expectations:
        assert _value(after, metric, **labels) > _value(before, metric, **labels), (metric, labels)
    assert "dbpilot_query_seconds_bucket" in after and "dbpilot_backup_bytes_total" in after
    assert re.search(
        r'dbpilot_http_requests_total\{[^}]*route="/api/v1/projects/\{project_id\}', after
    )


async def test_health_readiness_meta_and_system(ready: Stack) -> None:
    async with httpx.AsyncClient(base_url=ready.api_url) as anon:
        assert (await anon.get("/healthz")).json() == {"status": "ok"}
        ready_resp = await anon.get("/readyz")
        assert ready_resp.status_code == 200
        assert ready_resp.json()["checks"]["postgres"]["ok"] is True
        assert ready_resp.json()["checks"]["redis"]["ok"] is True
        meta = (await anon.get("/api/v1/meta")).json()
        assert (
            meta["engines"]["mongodb"]["ai_sql"] is False
            and meta["engines"]["postgresql"]["ai_sql"] is True
        )
        assert meta["engines"]["mssql"]["native_read_only_session"] is False
        resp = await anon.get("/api/v1/projects")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["cache-control"] == "no-store" and resp.headers["x-request-id"]
    system = await ready.ok("GET", "/api/v1/system")
    assert system["counts"]["users"] >= 1 and system["ai_provider"] == "mock"
    viewer, _ = await ready.member("viewer") if ready.project_id else await ready.user_in_org()
    assert (await viewer.get("/api/v1/system")).status_code == 403


async def test_the_error_body_is_always_the_same_shape(ready: Stack) -> None:
    resp = await ready.client.post(ready.p("/queries/sql"), json={"sql": ""})
    body = resp.json()
    assert resp.status_code == 422 and set(body["error"]) == {"code", "message", "details"}
    missing = await ready.client.get("/api/v1/nothing-here")
    assert missing.status_code == 404 and set(missing.json()["error"]) == {
        "code",
        "message",
        "details",
    }
