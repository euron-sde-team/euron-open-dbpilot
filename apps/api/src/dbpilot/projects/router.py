from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, Response

from dbpilot.auth.dependencies import CurrentUser, actor_of
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.projects import service
from dbpilot.projects.access import Admin, Owner, Viewer
from dbpilot.projects.schemas import (
    MemberAdd,
    MemberOut,
    MemberUpdate,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
)

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut], summary="Your projects")
async def list_projects(
    user: CurrentUser,
    session: SessionDep,
    org_id: uuid.UUID | None = Query(None, description="Only this organization's projects"),
) -> list[ProjectOut]:
    return await service.list_for(session, user, org_id)


@router.post(
    "",
    response_model=ProjectOut,
    status_code=201,
    summary="Create a project (organization owners and admins)",
)
async def create_project(
    body: ProjectCreate, request: Request, user: CurrentUser, session: SessionDep
) -> ProjectOut:
    project = await service.create(session, user, body, actor_of(request))
    return next(o for o in await service.list_for(session, user) if o.id == project.id)


@router.get("/{project_id}", response_model=ProjectOut, summary="One project")
async def get_project(p: Viewer, session: SessionDep) -> ProjectOut:
    return await service.out(session, p)


@router.patch("/{project_id}", response_model=ProjectOut, summary="Rename or describe a project")
async def update_project(body: ProjectUpdate, p: Admin, session: SessionDep) -> ProjectOut:
    await service.update(session, p, body)
    return await service.out(session, p)


@router.delete(
    "/{project_id}",
    status_code=204,
    summary="Delete a project with its connections, history and backups (owners)",
)
async def delete_project(p: Owner, session: SessionDep, container: ContainerDep) -> Response:
    await service.delete(container, session, p)
    return Response(status_code=204)


@router.get("/{project_id}/members", response_model=list[MemberOut], summary="Project members")
async def list_members(p: Viewer, session: SessionDep) -> list[MemberOut]:
    return [
        MemberOut(
            user_id=u.id,
            email=u.email,
            name=u.name,
            role=m.role,
            joined_at=m.created_at,
        )
        for m, u in await service.members(session, p.project_id)
    ]


@router.post(
    "/{project_id}/members",
    response_model=MemberOut,
    status_code=201,
    summary="Add a person from the organization",
)
async def add_member(body: MemberAdd, p: Admin, session: SessionDep) -> MemberOut:
    member, user = await service.add_member(session, p, body)
    return MemberOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=member.role,
        joined_at=member.created_at,
    )


@router.patch(
    "/{project_id}/members/{user_id}", response_model=MemberOut, summary="Change a member's role"
)
async def update_member(
    user_id: uuid.UUID, body: MemberUpdate, p: Admin, session: SessionDep
) -> MemberOut:
    member, user = await service.update_member(session, p, user_id, body.role)
    return MemberOut(
        user_id=user.id,
        email=user.email,
        name=user.name,
        role=member.role,
        joined_at=member.created_at,
    )


@router.delete(
    "/{project_id}/members/{user_id}",
    status_code=204,
    summary="Remove a member and their connection grants",
)
async def remove_member(user_id: uuid.UUID, p: Admin, session: SessionDep) -> Response:
    await service.remove_member(session, p, user_id)
    return Response(status_code=204)
