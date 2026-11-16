from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response

from dbpilot.auth.dependencies import CurrentUser, actor_of
from dbpilot.core.deps import SessionDep
from dbpilot.orgs import service
from dbpilot.orgs.schemas import (
    OrgCreate,
    OrgMemberAdd,
    OrgMemberOut,
    OrgMemberUpdate,
    OrgOut,
    OrgUpdate,
)

router = APIRouter(prefix="/api/v1/orgs", tags=["orgs"])


@router.get("", response_model=list[OrgOut], summary="Your organizations")
async def list_orgs(user: CurrentUser, session: SessionDep) -> list[OrgOut]:
    return await service.list_for(session, user)


@router.post("", response_model=OrgOut, status_code=201, summary="Create an organization")
async def create_org(
    body: OrgCreate, request: Request, user: CurrentUser, session: SessionDep
) -> OrgOut:
    org = await service.create_for(session, user, body.name, actor_of(request))
    await session.commit()
    return OrgOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        role="owner",
        members=1,
        created_at=org.created_at,
    )


@router.get("/{org_id}", response_model=OrgOut, summary="One organization")
async def get_org(org_id: uuid.UUID, user: CurrentUser, session: SessionDep) -> OrgOut:
    await service.get_for(session, user, org_id, "member")
    return next(o for o in await service.list_for(session, user) if o.id == org_id)


@router.patch("/{org_id}", response_model=OrgOut, summary="Rename an organization (owners, admins)")
async def update_org(
    org_id: uuid.UUID, body: OrgUpdate, request: Request, user: CurrentUser, session: SessionDep
) -> OrgOut:
    org, _ = await service.get_for(session, user, org_id, "admin")
    await service.rename(session, org, body.name, actor_of(request))
    return next(o for o in await service.list_for(session, user) if o.id == org_id)


@router.get("/{org_id}/members", response_model=list[OrgMemberOut], summary="Members")
async def list_members(
    org_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[OrgMemberOut]:
    await service.get_for(session, user, org_id, "member")
    return [
        OrgMemberOut(
            user_id=u.id,
            email=u.email,
            name=u.name,
            role=m.role,
            joined_at=m.created_at,
        )
        for m, u in await service.members(session, org_id)
    ]


@router.post(
    "/{org_id}/members",
    response_model=OrgMemberOut,
    status_code=201,
    summary="Add a person (a new account needs a name and a temporary password)",
)
async def add_member(
    org_id: uuid.UUID,
    body: OrgMemberAdd,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> OrgMemberOut:
    org, role = await service.get_for(session, user, org_id, "admin")
    member, person = await service.add_member(session, org, body, actor_of(request), role)
    return OrgMemberOut(
        user_id=person.id,
        email=person.email,
        name=person.name,
        role=member.role,
        joined_at=member.created_at,
    )


@router.patch("/{org_id}/members/{user_id}", response_model=OrgMemberOut, summary="Change a role")
async def update_member(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body: OrgMemberUpdate,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> OrgMemberOut:
    org, role = await service.get_for(session, user, org_id, "admin")
    member, person = await service.update_member(
        session, org, user_id, body.role, actor_of(request), role
    )
    return OrgMemberOut(
        user_id=person.id,
        email=person.email,
        name=person.name,
        role=member.role,
        joined_at=member.created_at,
    )


@router.delete(
    "/{org_id}/members/{user_id}",
    status_code=204,
    summary="Remove a person from the organization, its projects and their connection grants",
)
async def remove_member(
    org_id: uuid.UUID, user_id: uuid.UUID, request: Request, user: CurrentUser, session: SessionDep
) -> Response:
    org, role = await service.get_for(session, user, org_id, "admin")
    await service.remove_member(session, org, user_id, actor_of(request), role)
    return Response(status_code=204)
