from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from dbpilot.connections.access import accessible
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.monitoring import service
from dbpilot.monitoring.schemas import HealthCard
from dbpilot.projects.access import Viewer

router = APIRouter(prefix="/api/v1/projects/{project_id}/connections", tags=["monitoring"])


@router.get(
    "/{connection_id}/health",
    response_model=HealthCard,
    summary="Health card: reachability, size, connections, slow statements, index advisories "
    "and security checks read from the engine itself (cached 15 s)",
)
async def connection_health(
    connection_id: uuid.UUID,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
    refresh: bool = Query(False, description="Skip the short cache and probe now"),
) -> HealthCard:
    access = await accessible(session, p, connection_id)
    return await service.health(container, session, p, access, refresh=refresh)
