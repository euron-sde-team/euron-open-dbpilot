"""The per-connection health card: reachability, size, connections, slow statements, index
advisories and the security checks (AC13), computed live from the connected engine.

Caching (docs/architecture/HLD.md): the card is a short response cache in the key-value
store under ``health:<connection id>`` for ``HEALTH_CACHE_TTL_S`` (15 s), so a dashboard
polling it reaches the database at most once per window, however many people watch.
Changing or testing the connection drops the entry. A miss probes the engine through the
audited gateway (``connection.health_checked``), never a separately maintained rollup.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.connections import gateway
from dbpilot.connections.access import ConnectionAccess
from dbpilot.connections.connectors.health import HealthReport, mongo_health, redis_health
from dbpilot.connections.connectors.health_sql import sql_health
from dbpilot.connections.connectors.mongo import MongoReader
from dbpilot.connections.connectors.redis_source import RedisReader
from dbpilot.connections.service import friendly
from dbpilot.core import kv
from dbpilot.core.container import Container
from dbpilot.core.errors import AppError
from dbpilot.monitoring.schemas import HealthCard, SecurityCheck, SecuritySummary
from dbpilot.projects.access import Principal
from dbpilot.schema.schemas import SchemaObject

CACHE = "health"


def _summary(checks: list[SecurityCheck]) -> SecuritySummary:
    return SecuritySummary(
        passed=sum(c.status == "pass" for c in checks),
        warnings=sum(c.status == "warn" for c in checks),
        failures=sum(c.status == "fail" for c in checks),
        unknown=sum(c.status == "unknown" for c in checks),
    )


def schema_advisories(objects: list[SchemaObject]) -> list[dict[str, object]]:
    """Advisories any engine shares, read from the latest snapshot: foreign keys with no
    index, and large MongoDB collections with only the ``_id`` index."""
    out: list[dict[str, object]] = []
    for o in objects:
        if o.kind == "table":
            leads = {ix.columns[0].lower() for ix in o.indexes if ix.columns}
            leads |= {c.name.lower() for c in o.columns if c.primary_key}
            for fk in o.foreign_keys:
                if fk.columns and fk.columns[0].lower() not in leads:
                    out.append(
                        {
                            "table": o.qualified,
                            "columns": ", ".join(fk.columns),
                            "kind": "unindexed_foreign_key",
                            "reason": f"The foreign key to {fk.ref_table} has no index",
                            "source": "schema snapshot",
                        }
                    )
        if o.kind == "collection" and (o.row_count or 0) > 10_000 and len(o.indexes) <= 1:
            out.append(
                {
                    "table": o.name,
                    "kind": "collection_without_indexes",
                    "reason": f"{o.row_count:,} documents and no index besides _id",
                    "source": "schema snapshot",
                }
            )
    return out


async def health(
    container: Container,
    session: AsyncSession,
    p: Principal,
    access: ConnectionAccess,
    *,
    refresh: bool = False,
) -> HealthCard:
    from dbpilot.schema import service as schemas

    conn = access.connection
    ttl = container.settings.health_cache_ttl_s
    key = f"health:{conn.id}"
    if not refresh:
        cached = await kv.cache_get(container.kv, key, CACHE)
        if cached is not None:
            return HealthCard.model_validate({**orjson.loads(cached), "cached": True})
    ssl_mode = str((conn.config or {}).get("ssl_mode") or "disable")

    def probe(handle: gateway.Handle) -> HealthReport:
        if isinstance(handle, MongoReader):
            return mongo_health(handle, ssl_mode)
        if isinstance(handle, RedisReader):
            return redis_health(handle, ssl_mode, authenticated=conn.secret_sealed is not None)
        return sql_health(handle)

    started = time.perf_counter()
    error: str | None = None
    report = HealthReport(reachable=False)
    try:
        report = await gateway.read(
            container,
            session,
            conn,
            p.actor,
            action="connection.health_checked",
            work=probe,
            org_id=p.org_id,
            timeout_s=min(conn.timeout_s, 30),
            summarize=lambda r: {
                "security": {c.id: c.status for c in r.security},
                "reachable": r.reachable,
            },
        )
    except AppError as exc:
        error = exc.message
    except Exception as exc:  # unreachable, refused login, ...: the card says so
        error = friendly(gateway.error_text(exc))
    latest = await schemas.latest_row(session, conn.id)
    objects = [SchemaObject.model_validate(o) for o in latest.objects] if latest else []
    checks = [SecurityCheck.model_validate(asdict(c)) for c in report.security]
    card = HealthCard(
        connection_id=conn.id,
        engine=conn.kind,
        reachable=error is None and report.reachable,
        error=error,
        server_version=report.server_version or conn.server_version,
        read_only_session=report.read_only_session,
        latency_ms=round((time.perf_counter() - started) * 1000, 1),
        active_connections=report.active_connections,
        max_connections=report.max_connections,
        size_bytes=report.size_bytes,
        uptime_s=report.uptime_s,
        cache_hit_ratio=report.cache_hit_ratio,
        objects=len(objects) if latest else None,
        rows=sum(o.row_count or 0 for o in objects) if latest else None,
        slow_queries=report.slow_queries,
        slow_queries_source=report.slow_queries_source,
        slow_queries_note=report.slow_queries_note,
        index_advisories=[*report.index_advisories, *schema_advisories(objects)],
        security=checks,
        security_summary=_summary(checks),
        notes=report.notes,
        checked_at=datetime.now(UTC),
        cached=False,
        cache_ttl_s=ttl,
    )
    await kv.cache_set(container.kv, key, card.model_dump_json().encode(), ttl, CACHE)
    return card
