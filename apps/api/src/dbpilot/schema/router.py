from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from dbpilot.connections.access import accessible
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.core.schemas import Page
from dbpilot.projects.access import Viewer
from dbpilot.schema import service
from dbpilot.schema.schemas import Relationships, SearchHit, SnapshotOut, SnapshotSummary

router = APIRouter(prefix="/api/v1/projects/{project_id}/connections", tags=["schema"])


@router.get(
    "/{connection_id}/schema",
    response_model=SnapshotOut,
    summary="The discovered schema (latest snapshot; rediscovered when missing or stale)",
)
async def get_schema(
    connection_id: uuid.UUID, p: Viewer, session: SessionDep, container: ContainerDep
) -> SnapshotOut:
    access = await accessible(session, p, connection_id)
    return await service.ensure(container, session, access.connection, p.actor, org_id=p.org_id)


@router.post(
    "/{connection_id}/schema/refresh",
    response_model=SnapshotOut,
    summary="Discover the schema from the live database now (a new snapshot)",
)
async def refresh_schema(
    connection_id: uuid.UUID, p: Viewer, session: SessionDep, container: ContainerDep
) -> SnapshotOut:
    access = await accessible(session, p, connection_id)
    return await service.discover_now(
        container, session, access.connection, p.actor, org_id=p.org_id, trigger="refresh"
    )


@router.get(
    "/{connection_id}/schema/snapshots",
    response_model=Page[SnapshotSummary],
    summary="Schema snapshots, newest first",
)
async def list_snapshots(
    connection_id: uuid.UUID,
    p: Viewer,
    session: SessionDep,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> Page[SnapshotSummary]:
    access = await accessible(session, p, connection_id)
    items, nxt = await service.list_snapshots(session, access.connection, cursor, limit)
    return Page[SnapshotSummary](items=items, next_cursor=nxt)


@router.get(
    "/{connection_id}/schema/snapshots/{snapshot_id}",
    response_model=SnapshotOut,
    summary="One schema snapshot",
)
async def get_snapshot(
    connection_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
) -> SnapshotOut:
    access = await accessible(session, p, connection_id)
    return await service.get_snapshot(container, session, access.connection, snapshot_id)


@router.get(
    "/{connection_id}/schema/search",
    response_model=list[SearchHit],
    summary="Search tables, views, collections, columns, indexes and foreign keys by name",
)
async def search_schema(
    connection_id: uuid.UUID,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(100, ge=1, le=500),
) -> list[SearchHit]:
    access = await accessible(session, p, connection_id)
    snapshot = await service.ensure(container, session, access.connection, p.actor, org_id=p.org_id)
    return service.search(snapshot, q, limit)


@router.get(
    "/{connection_id}/schema/relationships",
    response_model=Relationships,
    summary="The ER view: tables with columns, foreign keys as edges (SQL engines)",
)
async def relationships(
    connection_id: uuid.UUID, p: Viewer, session: SessionDep, container: ContainerDep
) -> Relationships:
    access = await accessible(session, p, connection_id)
    snapshot = await service.ensure(container, session, access.connection, p.actor, org_id=p.org_id)
    return service.relationships(snapshot)
