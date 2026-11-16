from __future__ import annotations

import uuid

from fastapi import APIRouter

from dbpilot.auth.dependencies import CurrentUser
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.dashboard import service
from dbpilot.dashboard.schemas import DashboardOut
from dbpilot.orgs import service as orgs
from dbpilot.projects.access import Viewer

router = APIRouter(tags=["dashboard"])


@router.get(
    "/api/v1/orgs/{org_id}/dashboard",
    response_model=DashboardOut,
    summary="Organization totals, computed live (cached 15 s; owners and admins)",
)
async def org_dashboard(
    org_id: uuid.UUID, user: CurrentUser, session: SessionDep, container: ContainerDep
) -> DashboardOut:
    org, _ = await orgs.get_for(session, user, org_id, "admin")
    return await service.org_dashboard(container, session, org)


@router.get(
    "/api/v1/projects/{project_id}/overview",
    response_model=DashboardOut,
    summary="Project totals over the connections you can see, computed live (cached 15 s)",
)
async def project_overview(p: Viewer, session: SessionDep, container: ContainerDep) -> DashboardOut:
    return await service.project_overview(container, session, p)
