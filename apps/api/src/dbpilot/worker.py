"""The scheduled-backup worker: ``dbpilot worker``.

One loop, every ``WORKER_POLL_INTERVAL_S``:

1. record a heartbeat (``worker_heartbeats``; ``GET /api/v1/system`` shows it);
2. claim due backup schedules (``FOR UPDATE SKIP LOCKED``, ``next_run_at`` moved forward
   in the claiming transaction), run each backup through the same ``run_backup`` a manual
   backup uses (audited as ``system:scheduler``), then apply the schedule's retention;
3. mark backups whose process stopped as failed, and expire unconfirmed confirmations.

Stateless and safe to run as several replicas. SIGTERM stops claiming and lets the current
backup finish (up to the grace period). Prometheus metrics on ``WORKER_METRICS_PORT``
(``/metrics``), which is also its container health check: so the process exits with an
error the moment its loop ends without being asked to, rather than answering health checks
while doing nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import start_http_server
from sqlalchemy.dialects.postgresql import insert

from dbpilot import __version__
from dbpilot.audit.service import SCHEDULER
from dbpilot.backups import schedules
from dbpilot.backups.service import run_backup
from dbpilot.confirmations.service import expire_stale
from dbpilot.core import metrics
from dbpilot.core.config import Settings, get_settings
from dbpilot.core.container import Container
from dbpilot.core.db import import_all_models
from dbpilot.core.logs import configure_logging
from dbpilot.system.models import WorkerHeartbeat

log = logging.getLogger("dbpilot.worker")
SHUTDOWN_GRACE_S = 60.0


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


class Worker:
    def __init__(self, container: Container, settings: Settings, name: str | None = None) -> None:
        self.container = container
        self.settings = settings
        self.name = name or worker_name()
        self.started_at = datetime.now(UTC)
        self.stats: dict[str, Any] = {"loops": 0, "backups": 0, "backup_failures": 0, "errors": 0}

    async def heartbeat(self) -> None:
        async with self.container.db.sessionmaker() as session:
            values = {
                "name": self.name,
                "started_at": self.started_at,
                "last_seen_at": datetime.now(UTC),
                "version": __version__,
                "stats": self.stats,
            }
            stmt = insert(WorkerHeartbeat).values(**values)
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[WorkerHeartbeat.name],
                    set_={"last_seen_at": stmt.excluded.last_seen_at, "stats": stmt.excluded.stats},
                )
            )
            await session.commit()

    async def run_due(self) -> int:
        """Run every due schedule this worker claims; returns how many ran."""
        async with self.container.db.sessionmaker() as session:
            due = await schedules.claim_due(session, self.settings.worker_batch)
        for item in due:
            async with self.container.db.sessionmaker() as session:
                backup = await run_backup(
                    self.container,
                    session,
                    item.connection,
                    SCHEDULER,
                    org_id=item.org_id,
                    trigger="scheduled",
                    schedule_id=item.schedule_id,
                )
                await schedules.finish_run(session, item.schedule_id, backup.status)
                self.stats["backups"] += 1
                if backup.status != "succeeded":
                    self.stats["backup_failures"] += 1
                metrics.SCHEDULED_BACKUPS.labels(backup.status).inc()
                log.info(
                    "scheduled backup",
                    extra={"connection": str(item.connection.id), "status": backup.status},
                )
                pruned = await schedules.apply_retention(self.container, session, item)
                if pruned:
                    log.info(
                        "retention", extra={"connection": str(item.connection.id), "pruned": pruned}
                    )
        return len(due)

    async def housekeeping(self) -> None:
        async with self.container.db.sessionmaker() as session:
            stale = await schedules.fail_stale(
                session, timedelta(minutes=self.settings.backup_stale_minutes)
            )
            expired = await expire_stale(session)
        if stale or expired:
            log.info(
                "housekeeping", extra={"stale_backups": stale, "expired_confirmations": expired}
            )

    async def tick(self) -> int:
        self.stats["loops"] += 1
        try:
            # Before the work (the worker is alive even during a long backup) and after it
            # (the recorded totals include what this loop did).
            await self.heartbeat()
            ran = await self.run_due()
            await self.housekeeping()
            await self.heartbeat()
        except Exception:
            self.stats["errors"] += 1
            metrics.WORKER_TICKS.labels("error").inc()
            log.exception("worker loop failed")
            return 0
        metrics.WORKER_TICKS.labels("ok").inc()
        return ran

    async def serve(self, stop: asyncio.Event) -> None:
        log.info(
            "worker started",
            extra={"worker": self.name, "poll_s": self.settings.worker_poll_interval_s},
        )
        while not stop.is_set():
            ran = await self.tick()
            delay = 0.1 if ran else self.settings.worker_poll_interval_s
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
        log.info("worker stopped", extra={"worker": self.name})


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    import_all_models()
    if settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port)
    container = Container.build(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    worker = Worker(container, settings)
    task = asyncio.create_task(worker.serve(stop))
    stopping = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({task, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if not stop.is_set():
            # The loop ended on its own: exit with its error so the orchestrator restarts
            # the worker (its health check must not outlive the loop).
            stopping.cancel()
            task.result()
            raise RuntimeError("the worker loop ended without being asked to stop")
        await asyncio.wait_for(task, timeout=SHUTDOWN_GRACE_S)
    except TimeoutError:
        task.cancel()
    finally:
        await container.close()
