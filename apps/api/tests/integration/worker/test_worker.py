"""AC12 (the worker): ``dbpilot worker`` runs due backup schedules, keeps their retention,
reaps backups whose process stopped and expires unconfirmed confirmations. These tests run
the real ``Worker`` against the real database and demo source."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from dbpilot.core.container import Container
from dbpilot.worker import Worker
from tests.integration.conftest import Sources, Stack

pytestmark = pytest.mark.integration


async def _sql(stack: Stack, statement: str, **params: object) -> list[tuple[object, ...]]:
    engine = create_async_engine(stack.settings.database_url)
    async with engine.begin() as c:
        result = await c.execute(sa.text(statement), params)
        rows = [tuple(r) for r in result.all()] if result.returns_rows else []
    await engine.dispose()
    return rows


async def test_ac12_the_worker_runs_a_due_schedule_keeps_retention_and_beats(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg())
    schedule = await ready.ok(
        "POST",
        ready.p("/backup-schedules"),
        {"connection_id": conn["id"], "interval_minutes": 60, "retention_count": 1},
        status=201,
    )
    container = Container.build(ready.settings)
    worker = Worker(container, ready.settings, name="test-worker")
    try:
        assert await worker.tick() == 0  # not due yet
        await _sql(
            ready,
            "UPDATE backup_schedules SET next_run_at = now() - interval '1 minute' WHERE id = :i",
            i=schedule["id"],
        )
        assert await worker.tick() == 1
        backups = (await ready.ok("GET", ready.p("/backups")))["items"]
        assert len(backups) == 1 and backups[0]["trigger"] == "scheduled"
        assert backups[0]["status"] == "succeeded" and backups[0]["schedule_id"] == schedule["id"]
        assert backups[0]["created_by_label"] == "system:scheduler"
        shown = await ready.ok("GET", ready.p(f"/backup-schedules/{schedule['id']}"))
        assert shown["last_status"] == "succeeded" and shown["next_run_at"] > shown["last_run_at"]
        # A second due run keeps only the newest (retention 1).
        await _sql(
            ready,
            "UPDATE backup_schedules SET next_run_at = now() - interval '1 minute' WHERE id = :i",
            i=schedule["id"],
        )
        assert await worker.tick() == 1
        backups = (await ready.ok("GET", ready.p("/backups")))["items"]
        assert len(backups) == 1
        assert await ready.audit(action="backup.pruned")
        beat = await _sql(
            ready, "SELECT name, stats FROM worker_heartbeats WHERE name = 'test-worker'"
        )
        assert beat and beat[0][1]["backups"] == 2 and beat[0][1]["loops"] == 3
        system = await ready.ok("GET", "/api/v1/system")
        assert any(w["name"] == "test-worker" and w["alive"] for w in system["workers"])
    finally:
        await container.close()


async def test_the_worker_reaps_stopped_backups_and_expires_confirmations(
    ready: Stack, sources: Sources
) -> None:
    conn = await ready.connection(sources.pg(admin=True))
    await _sql(
        ready,
        "INSERT INTO backups (id, project_id, connection_id, engine, trigger, status, started_at, format, "
        "table_count, row_count, tables, created_by_label) VALUES (gen_random_uuid(), :p, :c, 'postgresql', "
        "'manual', 'running', now() - interval '1 day', 'dbpilot-dump/1', 0, 0, '{}', 'x')",
        p=ready.project_id,
        c=conn["id"],
    )
    prepared = await ready.ok(
        "POST",
        ready.p("/writes"),
        {"connection_id": conn["id"], "sql": "INSERT INTO notes (body) VALUES ('never')"},
        status=201,
    )
    await _sql(
        ready,
        "UPDATE confirmations SET expires_at = now() - interval '1 minute' WHERE id = :i",
        i=prepared["id"],
    )
    container = Container.build(ready.settings)
    try:
        await Worker(container, ready.settings, name="reaper").tick()
    finally:
        await container.close()
    backups = (await ready.ok("GET", ready.p("/backups")))["items"]
    assert backups[0]["status"] == "failed" and "stopped" in backups[0]["error"]
    assert (await ready.ok("GET", ready.p(f"/confirmations/{prepared['id']}")))[
        "status"
    ] == "expired"
    assert await ready.audit(action="write.expired")


async def test_ac12_the_serving_loop_runs_until_asked_to_stop(
    ready: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    """``serve`` itself, not only one ``tick``: it starts, beats, and stops when told."""
    import asyncio

    container = Container.build(ready.settings)
    worker = Worker(container, ready.settings, name="serving-worker")
    stop = asyncio.Event()
    caplog.set_level("INFO", logger="dbpilot.worker")
    task = asyncio.create_task(worker.serve(stop))
    try:
        for _ in range(100):
            if await _sql(ready, "SELECT 1 FROM worker_heartbeats WHERE name = 'serving-worker'"):
                break
            assert not task.done(), task.exception()
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("the loop never beat")
        stop.set()
        await asyncio.wait_for(task, timeout=30)
    finally:
        await container.close()
    messages = [r.getMessage() for r in caplog.records if r.name == "dbpilot.worker"]
    assert messages[0] == "worker started" and messages[-1] == "worker stopped"
