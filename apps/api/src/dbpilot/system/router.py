"""Health, readiness, metrics, deployment info and the engine capability matrix."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text

from dbpilot import __version__
from dbpilot.auth.dependencies import InstanceAdmin
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.core.schemas import ApiModel
from dbpilot.system.models import WorkerHeartbeat

router = APIRouter(tags=["system"])

# What each engine supports in 0.1.0 (AC11). MongoDB and Redis have their own read-only
# surfaces; SQL-only features (the SQL editor, AI to SQL, schema comparison, backups) do
# not apply to them.
CAPABILITIES: dict[str, dict[str, bool]] = {
    "postgresql": {
        "explorer": True,
        "relationships": True,
        "sql_editor": True,
        "ai_sql": True,
        "monitoring": True,
        "backup_restore": True,
        "schema_compare": True,
        "confirmed_writes": True,
        "native_read_only_session": True,
    },
    "mysql": {
        "explorer": True,
        "relationships": True,
        "sql_editor": True,
        "ai_sql": True,
        "monitoring": True,
        "backup_restore": True,
        "schema_compare": True,
        "confirmed_writes": True,
        "native_read_only_session": True,
    },
    "sqlite": {
        "explorer": True,
        "relationships": True,
        "sql_editor": True,
        "ai_sql": True,
        "monitoring": True,
        "backup_restore": True,
        "schema_compare": True,
        "confirmed_writes": True,
        "native_read_only_session": True,
    },
    "mssql": {
        "explorer": True,
        "relationships": True,
        "sql_editor": True,
        "ai_sql": True,
        "monitoring": True,
        "backup_restore": True,
        "schema_compare": True,
        "confirmed_writes": True,
        "native_read_only_session": False,
    },
    "mongodb": {
        "explorer": True,
        "relationships": False,
        "sql_editor": False,
        "ai_sql": False,
        "monitoring": True,
        "backup_restore": False,
        "schema_compare": False,
        "confirmed_writes": False,
        "native_read_only_session": False,
    },
    "redis": {
        "explorer": True,
        "relationships": False,
        "sql_editor": False,
        "ai_sql": False,
        "monitoring": True,
        "backup_restore": False,
        "schema_compare": False,
        "confirmed_writes": False,
        "native_read_only_session": False,
    },
}


@router.get("/healthz", summary="Liveness")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _probe(name: str, coro: Awaitable[Any]) -> tuple[str, dict[str, Any]]:
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(coro, timeout=2)
        return name, {"ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:
        return name, {"ok": False, "error": type(exc).__name__}


@router.get("/readyz", summary="Readiness: DBPilot's Postgres reachable (Redis reported too)")
async def readyz(container: ContainerDep, response: Response) -> dict[str, Any]:
    async def pg() -> None:
        async with container.db.engine.connect() as conn:
            await conn.execute(text("select 1"))

    probes = [_probe("postgres", pg())]
    if container.kv.backend == "redis":
        probes.append(_probe("redis", container.kv.ping()))
    checks = dict(await asyncio.gather(*probes))
    # Redis holds caches and rate limits that fall back to process memory, so Postgres
    # alone decides readiness and Redis reports as degraded.
    ok = checks["postgres"]["ok"]
    response.status_code = 200 if ok else 503
    degraded = not all(c["ok"] for c in checks.values())
    return {
        "status": "degraded" if degraded else ("ready" if ok else "unavailable"),
        "checks": checks,
    }


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class MetaOut(ApiModel):
    name: str
    version: str
    engines: dict[str, dict[str, bool]]
    ai_enabled: bool


@router.get("/api/v1/meta", response_model=MetaOut, summary="Version and what each engine supports")
async def meta(container: ContainerDep) -> MetaOut:
    return MetaOut(
        name="Euron Open DBPilot",
        version=__version__,
        engines=CAPABILITIES,
        ai_enabled=container.settings.ai_provider != "none",
    )


class WorkerOut(ApiModel):
    name: str
    started_at: datetime
    last_seen_at: datetime
    alive: bool
    version: str
    stats: dict[str, Any]


class SystemOut(ApiModel):
    version: str
    environment: str
    storage: str
    key_mode: str
    cache: str
    ai_provider: str
    redis: bool
    workers: list[WorkerOut]
    counts: dict[str, int]


@router.get(
    "/api/v1/system",
    response_model=SystemOut,
    summary="Deployment state and worker heartbeats (instance administrators)",
)
async def system(_: InstanceAdmin, session: SessionDep, container: ContainerDep) -> SystemOut:
    from dbpilot.backups.models import Backup
    from dbpilot.connections.models import Connection
    from dbpilot.orgs.models import Organization, User
    from dbpilot.projects.models import Project
    from dbpilot.queries.models import QueryRun

    s = container.settings
    try:
        await container.kv.ping()
        redis_ok = container.kv.backend == "redis"
    except Exception:
        redis_ok = False
    alive_after = datetime.now(UTC) - timedelta(seconds=max(s.worker_poll_interval_s * 3, 30))
    beats = (
        await session.scalars(
            select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(50)
        )
    ).all()
    counts = {}
    for label, model in (
        ("organizations", Organization),
        ("projects", Project),
        ("users", User),
        ("connections", Connection),
        ("queries", QueryRun),
        ("backups", Backup),
    ):
        counts[label] = int(await session.scalar(select(func.count()).select_from(model)) or 0)
    return SystemOut(
        version=__version__,
        environment=s.environment,
        storage=s.storage,
        key_mode=s.key_mode,
        cache=s.cache_backend,
        ai_provider=s.ai_provider,
        redis=redis_ok,
        workers=[
            WorkerOut(
                name=b.name,
                started_at=b.started_at,
                last_seen_at=b.last_seen_at,
                alive=b.last_seen_at >= alive_after,
                version=b.version,
                stats=b.stats or {},
            )
            for b in beats
        ],
        counts=counts,
    )
