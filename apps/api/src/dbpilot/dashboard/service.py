"""Dashboard aggregates, live (AC12).

Caching (docs/architecture/HLD.md): a 15 s response cache (``DASHBOARD_CACHE_TTL_S``) in
the key-value store. The org dashboard is cached per organization under
``dash:org:<org id>``; a project overview per project and audience under
``dash:project:<project id>:<admin | user id>``, because a viewer only counts the
connections granted to them. A miss runs ``repository.totals``: one aggregate query
against DBPilot's own Postgres. The numbers can be at most one TTL old; nothing else needs
invalidating, and no worker maintains them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.connections.access import visible_ids
from dbpilot.core import kv
from dbpilot.core.container import Container
from dbpilot.dashboard import repository
from dbpilot.dashboard.schemas import DashboardOut, Totals
from dbpilot.orgs.models import Organization
from dbpilot.projects.access import Principal

CACHE = "dashboard"


async def _cached(container: Container, key: str) -> DashboardOut | None:
    raw = await kv.cache_get(container.kv, key, CACHE)
    if raw is None:
        return None
    return DashboardOut.model_validate({**orjson.loads(raw), "cached": True})


async def _store(container: Container, key: str, out: DashboardOut) -> DashboardOut:
    await kv.cache_set(
        container.kv,
        key,
        out.model_dump_json().encode(),
        container.settings.dashboard_cache_ttl_s,
        CACHE,
    )
    return out


async def org_dashboard(
    container: Container, session: AsyncSession, org: Organization
) -> DashboardOut:
    key = f"dash:org:{org.id}"
    if (hit := await _cached(container, key)) is not None:
        return hit
    project_ids = await repository.org_project_ids(session, org.id)
    values = await repository.totals(session, project_ids, None)
    out = DashboardOut(
        scope="org",
        org_id=org.id,
        project_id=None,
        totals=Totals(projects=len(project_ids), **values),
        computed_at=datetime.now(UTC),
        cached=False,
        cache_ttl_s=container.settings.dashboard_cache_ttl_s,
    )
    return await _store(container, key, out)


async def project_overview(
    container: Container, session: AsyncSession, p: Principal
) -> DashboardOut:
    audience = "admin" if p.manages else str(p.user_id)
    key = f"dash:project:{p.project_id}:{audience}"
    if (hit := await _cached(container, key)) is not None:
        return hit
    ids = await visible_ids(session, p)
    values = await repository.totals(session, [p.project_id], ids)
    out = DashboardOut(
        scope="project",
        org_id=p.org_id,
        project_id=p.project_id,
        totals=Totals(projects=None, **values),
        computed_at=datetime.now(UTC),
        cached=False,
        cache_ttl_s=container.settings.dashboard_cache_ttl_s,
    )
    return await _store(container, key, out)
