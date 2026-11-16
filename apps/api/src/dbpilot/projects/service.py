"""Projects and their members."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import Actor, record
from dbpilot.core.container import Container
from dbpilot.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from dbpilot.core.text import slugify
from dbpilot.orgs import service as orgs
from dbpilot.orgs.models import OrgMember, User
from dbpilot.projects.access import Principal
from dbpilot.projects.models import Project, ProjectMember
from dbpilot.projects.schemas import MemberAdd, ProjectCreate, ProjectOut, ProjectUpdate


async def _unique_slug(session: AsyncSession, org_id: uuid.UUID, base: str) -> str:
    slug, n = base, 2
    while await session.scalar(
        select(Project.id).where(Project.org_id == org_id, Project.slug == slug)
    ):
        slug = f"{base[:56]}-{n}"
        n += 1
    return slug


async def create(session: AsyncSession, user: User, data: ProjectCreate, actor: Actor) -> Project:
    """Org owners and admins create projects (they act as owners of every project)."""
    await orgs.get_for(session, user, data.org_id, "admin")
    project = Project(
        org_id=data.org_id,
        name=data.name.strip(),
        slug=await _unique_slug(session, data.org_id, slugify(data.name, "project")),
        description=data.description,
        created_by=user.id,
    )
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    record(
        session,
        actor,
        "project.created",
        target_type="project",
        target_id=project.id,
        org_id=project.org_id,
        project_id=project.id,
        name=project.name,
    )
    await session.commit()
    return project


async def _roles(session: AsyncSession, user: User) -> list[tuple[Project, str]]:
    if user.role == "admin":
        projects = (await session.scalars(select(Project).order_by(Project.name))).all()
        return [(p, "owner") for p in projects]
    managed_orgs = select(OrgMember.org_id).where(
        OrgMember.user_id == user.id, OrgMember.role.in_(("owner", "admin"))
    )
    rows = (
        await session.execute(
            select(Project, ProjectMember.role, OrgMember.role)
            .join(
                OrgMember,
                (OrgMember.org_id == Project.org_id) & (OrgMember.user_id == user.id),
            )
            .outerjoin(
                ProjectMember,
                (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == user.id),
            )
            .where(or_(Project.org_id.in_(managed_orgs), ProjectMember.user_id == user.id))
            .order_by(Project.name, Project.id)
        )
    ).all()
    return [
        (p, "owner" if org_role in ("owner", "admin") else str(role)) for p, role, org_role in rows
    ]


async def list_for(
    session: AsyncSession, user: User, org_id: uuid.UUID | None = None
) -> list[ProjectOut]:
    """The caller's projects with member counts and the connections they can see (three
    grouped queries, whatever the number of projects)."""
    from dbpilot.connections import access as connection_access

    rows = [(p, r) for p, r in await _roles(session, user) if org_id is None or p.org_id == org_id]
    ids = [p.id for p, _ in rows]
    if not ids:
        return []
    members = {
        k: int(v)
        for k, v in (
            await session.execute(
                select(ProjectMember.project_id, func.count())
                .where(ProjectMember.project_id.in_(ids))
                .group_by(ProjectMember.project_id)
            )
        ).all()
    }
    managed = [p.id for p, r in rows if r in ("admin", "owner")]
    visible = await connection_access.visible_counts(session, user.id, ids, managed)
    return [
        ProjectOut(
            id=p.id,
            org_id=p.org_id,
            name=p.name,
            slug=p.slug,
            description=p.description,
            role=role,
            connections=visible.get(p.id, 0),
            members=members.get(p.id, 0),
            created_at=p.created_at,
        )
        for p, role in rows
    ]


async def out(session: AsyncSession, p: Principal) -> ProjectOut:
    return next(o for o in await list_for(session, p.user) if o.id == p.project_id)


async def update(session: AsyncSession, p: Principal, data: ProjectUpdate) -> None:
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name"):
        p.project.name = changes["name"].strip()
    if changes.get("description") is not None:
        p.project.description = changes["description"]
    record(
        session,
        p.actor,
        "project.updated",
        target_type="project",
        target_id=p.project_id,
        fields=sorted(changes),
        **p.scope(),
    )
    await session.commit()


async def delete(container: Container, session: AsyncSession, p: Principal) -> None:
    """Delete the project with its connections, history and backups. Stored backup files
    and uploaded SQLite files are removed after the commit; the audit log keeps every
    entry (it is append-only)."""
    from dbpilot.backups.models import Backup
    from dbpilot.connections.models import Connection

    keys = [
        k
        for k in (
            await session.scalars(
                select(Backup.storage_key).where(Backup.project_id == p.project_id)
            )
        ).all()
        if k
    ]
    keys += [
        k
        for k in (
            await session.scalars(
                select(Connection.file_key).where(Connection.project_id == p.project_id)
            )
        ).all()
        if k
    ]
    record(
        session,
        p.actor,
        "project.deleted",
        target_type="project",
        target_id=p.project_id,
        name=p.project.name,
        **p.scope(),
    )
    await session.delete(p.project)
    await session.commit()
    for key in keys:
        await container.storage.delete(key)


async def members(session: AsyncSession, project_id: uuid.UUID) -> list[tuple[ProjectMember, User]]:
    result = await session.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(User.name, User.id)
    )
    return [(m, u) for m, u in result.all()]


async def add_member(
    session: AsyncSession, p: Principal, data: MemberAdd
) -> tuple[ProjectMember, User]:
    if data.role == "owner" and not p.at_least("owner"):
        raise PermissionDeniedError("Only owners can add owners", code="insufficient_role")
    user = await orgs.user_by_email(session, data.email)
    if user is None or await session.get(OrgMember, (p.org_id, user.id)) is None:
        raise NotFoundError(
            "Nobody with this email is in the project's organization. Add them there first",
            code="user_not_in_org",
        )
    if await session.get(ProjectMember, (p.project_id, user.id)):
        raise ConflictError("This person is already in the project", code="already_member")
    member = ProjectMember(project_id=p.project_id, user_id=user.id, role=data.role)
    session.add(member)
    record(
        session,
        p.actor,
        "project.member_added",
        target_type="user",
        target_id=user.id,
        email=user.email,
        role=data.role,
        **p.scope(),
    )
    await session.commit()
    await session.refresh(member)
    return member, user


async def update_member(
    session: AsyncSession, p: Principal, user_id: uuid.UUID, role: str
) -> tuple[ProjectMember, User]:
    member = await session.get(ProjectMember, (p.project_id, user_id))
    user = await session.get(User, user_id)
    if member is None or user is None:
        raise NotFoundError("Member not found")
    if "owner" in (role, member.role) and not p.at_least("owner"):
        raise PermissionDeniedError("Only owners can change owners", code="insufficient_role")
    member.role = role
    record(
        session,
        p.actor,
        "project.member_updated",
        target_type="user",
        target_id=user_id,
        role=role,
        **p.scope(),
    )
    await session.commit()
    return member, user


async def remove_member(session: AsyncSession, p: Principal, user_id: uuid.UUID) -> None:
    from dbpilot.connections.models import ConnectionGrant

    member = await session.get(ProjectMember, (p.project_id, user_id))
    if member is None:
        raise NotFoundError("Member not found")
    if member.role == "owner" and not p.at_least("owner"):
        raise PermissionDeniedError("Only owners can remove owners", code="insufficient_role")
    for grant in (
        await session.scalars(
            select(ConnectionGrant).where(
                ConnectionGrant.project_id == p.project_id, ConnectionGrant.user_id == user_id
            )
        )
    ).all():
        await session.delete(grant)
    await session.delete(member)
    record(
        session,
        p.actor,
        "project.member_removed",
        target_type="user",
        target_id=user_id,
        **p.scope(),
    )
    await session.commit()
