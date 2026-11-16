"""Organizations (tenants) and their members.

Isolation is structural: projects carry ``org_id``, and every connection, query, backup and
audit entry is reached through its project, which ``projects.access`` resolves only for
people in that project's organization. Guards keep at least one owner in every org.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import Actor, record
from dbpilot.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from dbpilot.core.security import hash_password, password_problems
from dbpilot.core.text import slugify
from dbpilot.orgs.models import Organization, OrgMember, User
from dbpilot.orgs.schemas import OrgMemberAdd, OrgOut

ORG_RANK = {"member": 0, "admin": 1, "owner": 2}


async def user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.email) == email.lower()))


async def unique_slug(session: AsyncSession, base: str) -> str:
    slug, n = base, 2
    while await session.scalar(select(Organization.id).where(Organization.slug == slug)):
        slug = f"{base[:56]}-{n}"
        n += 1
    return slug


async def create_for(session: AsyncSession, user: User, name: str, actor: Actor) -> Organization:
    """A new organization owned by ``user`` (flushed, not committed)."""
    org = Organization(
        name=name.strip()[:120],
        slug=await unique_slug(session, slugify(name, "org")),
        created_by=user.id,
    )
    session.add(org)
    await session.flush()
    session.add(OrgMember(org_id=org.id, user_id=user.id, role="owner"))
    record(
        session,
        actor,
        "org.created",
        target_type="organization",
        target_id=org.id,
        org_id=org.id,
        name=org.name,
    )
    return org


async def role_in(session: AsyncSession, user: User, org_id: uuid.UUID) -> str | None:
    """The user's role in the organization; instance admins are owners everywhere."""
    if user.role == "admin":
        return "owner"
    member = await session.get(OrgMember, (org_id, user.id))
    return member.role if member else None


async def get_for(
    session: AsyncSession, user: User, org_id: uuid.UUID, at_least: str
) -> tuple[Organization, str]:
    """The organization and the user's role in it: 404 when they are not in it (its
    existence does not leak), 403 when their role is below ``at_least``."""
    org = await session.get(Organization, org_id)
    role = await role_in(session, user, org_id) if org else None
    if org is None or role is None:
        raise NotFoundError("Organization not found")
    if ORG_RANK[role] < ORG_RANK[at_least]:
        raise PermissionDeniedError(
            f"This needs the {at_least} role in the organization", code="insufficient_org_role"
        )
    return org, role


async def list_for(session: AsyncSession, user: User) -> list[OrgOut]:
    """The user's organizations with project and member counts (two grouped queries)."""
    from dbpilot.projects.models import Project

    if user.role == "admin":
        orgs = (await session.scalars(select(Organization).order_by(Organization.name))).all()
        rows = [(o, "owner") for o in orgs]
    else:
        result = await session.execute(
            select(Organization, OrgMember.role)
            .join(OrgMember, OrgMember.org_id == Organization.id)
            .where(OrgMember.user_id == user.id)
            .order_by(Organization.name)
        )
        rows = [(o, str(r)) for o, r in result.all()]
    ids = [o.id for o, _ in rows]
    projects: dict[uuid.UUID, int] = {}
    members: dict[uuid.UUID, int] = {}
    if ids:
        projects = {
            k: int(v)
            for k, v in (
                await session.execute(
                    select(Project.org_id, func.count())
                    .where(Project.org_id.in_(ids))
                    .group_by(Project.org_id)
                )
            ).all()
        }
        members = {
            k: int(v)
            for k, v in (
                await session.execute(
                    select(OrgMember.org_id, func.count())
                    .where(OrgMember.org_id.in_(ids))
                    .group_by(OrgMember.org_id)
                )
            ).all()
        }
    return [
        OrgOut(
            id=o.id,
            name=o.name,
            slug=o.slug,
            role=role,
            projects=projects.get(o.id, 0),
            members=members.get(o.id, 0),
            created_at=o.created_at,
        )
        for o, role in rows
    ]


async def rename(session: AsyncSession, org: Organization, name: str, actor: Actor) -> Organization:
    org.name = name.strip()
    record(
        session,
        actor,
        "org.updated",
        target_type="organization",
        target_id=org.id,
        org_id=org.id,
        name=org.name,
    )
    await session.commit()
    await session.refresh(org)
    return org


async def members(session: AsyncSession, org_id: uuid.UUID) -> Sequence[tuple[OrgMember, User]]:
    result = await session.execute(
        select(OrgMember, User)
        .join(User, User.id == OrgMember.user_id)
        .where(OrgMember.org_id == org_id)
        .order_by(User.name, User.id)
    )
    return [(m, u) for m, u in result.all()]


async def add_member(
    session: AsyncSession, org: Organization, data: OrgMemberAdd, actor: Actor, caller_role: str
) -> tuple[OrgMember, User]:
    if data.role == "owner" and caller_role != "owner":
        raise PermissionDeniedError("Only owners can add owners", code="insufficient_org_role")
    user = await user_by_email(session, data.email)
    if user is None:
        if not (data.name and data.password):
            raise NotFoundError(
                "No account has this email. Give a name and a temporary password to create one",
                code="user_not_found",
            )
        if problems := password_problems(data.password):
            raise AppError(
                "Password needs " + ", ".join(problems),
                code="weak_password",
                details={"problems": problems},
            )
        user = User(
            email=data.email.lower(),
            name=data.name.strip(),
            role="user",
            password_hash=await asyncio.to_thread(hash_password, data.password),
        )
        session.add(user)
        await session.flush()
        record(
            session,
            actor,
            "user.created",
            target_type="user",
            target_id=user.id,
            org_id=org.id,
            email=user.email,
        )
    elif await session.get(OrgMember, (org.id, user.id)):
        raise ConflictError("This person is already in the organization", code="already_member")
    member = OrgMember(org_id=org.id, user_id=user.id, role=data.role)
    session.add(member)
    record(
        session,
        actor,
        "org.member_added",
        target_type="user",
        target_id=user.id,
        org_id=org.id,
        email=user.email,
        role=data.role,
    )
    await session.commit()
    await session.refresh(member)
    return member, user


async def _owner_count(session: AsyncSession, org_id: uuid.UUID) -> int:
    rows = await session.scalars(
        select(OrgMember.user_id)
        .where(OrgMember.org_id == org_id, OrgMember.role == "owner")
        .with_for_update()
    )
    return len(rows.all())


async def update_member(
    session: AsyncSession,
    org: Organization,
    user_id: uuid.UUID,
    role: str,
    actor: Actor,
    caller_role: str,
) -> tuple[OrgMember, User]:
    member = await session.get(OrgMember, (org.id, user_id))
    user = await session.get(User, user_id)
    if member is None or user is None:
        raise NotFoundError("Member not found")
    if "owner" in (role, member.role) and caller_role != "owner":
        raise PermissionDeniedError("Only owners can change owners", code="insufficient_org_role")
    if member.role == "owner" and role != "owner" and await _owner_count(session, org.id) <= 1:
        raise ConflictError("An organization needs at least one owner", code="last_owner")
    member.role = role
    record(
        session,
        actor,
        "org.member_updated",
        target_type="user",
        target_id=user_id,
        org_id=org.id,
        role=role,
    )
    await session.commit()
    return member, user


async def remove_member(
    session: AsyncSession, org: Organization, user_id: uuid.UUID, actor: Actor, caller_role: str
) -> None:
    from dbpilot.connections.models import ConnectionGrant
    from dbpilot.projects.models import Project, ProjectMember

    member = await session.get(OrgMember, (org.id, user_id))
    if member is None:
        raise NotFoundError("Member not found")
    if member.role == "owner" and caller_role != "owner":
        raise PermissionDeniedError("Only owners can remove owners", code="insufficient_org_role")
    if member.role == "owner" and await _owner_count(session, org.id) <= 1:
        raise ConflictError("An organization needs at least one owner", code="last_owner")
    # Leaving the organization also leaves every project in it, with every grant.
    project_ids = select(Project.id).where(Project.org_id == org.id).scalar_subquery()
    for pm in (
        await session.scalars(
            select(ProjectMember).where(
                ProjectMember.user_id == user_id, ProjectMember.project_id.in_(project_ids)
            )
        )
    ).all():
        await session.delete(pm)
    for grant in (
        await session.scalars(
            select(ConnectionGrant).where(
                ConnectionGrant.user_id == user_id, ConnectionGrant.project_id.in_(project_ids)
            )
        )
    ).all():
        await session.delete(grant)
    await session.delete(member)
    record(
        session,
        actor,
        "org.member_removed",
        target_type="user",
        target_id=user_id,
        org_id=org.id,
    )
    await session.commit()
