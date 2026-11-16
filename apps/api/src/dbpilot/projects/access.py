"""Who may do what in a project. Every project route resolves a ``Principal`` first.

Project roles: ``viewer`` < ``admin`` < ``owner``. Owners and admins of the project's
organization, and instance administrators, act as project owners.

* viewer: sees the connections they hold a grant on; browses schemas, runs read-only
  queries, uses the AI assistant, reads health, backups and history of those connections.
  A grant may also let them prepare writes on that connection (``can_write``).
* admin: everything a viewer does on every connection of the project, plus managing
  connections, grants, members (not owners), backups, restores, schedules, schema
  comparisons, and reading the audit log.
* owner: everything, including owners and deleting the project.

A project the caller cannot see answers 404, never 403, so its existence does not leak.
Connection-level access (grants) is resolved by ``connections.access``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Path, Request

from dbpilot.audit.service import Actor
from dbpilot.auth.dependencies import current_user
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.core.errors import NotFoundError, PermissionDeniedError
from dbpilot.orgs import service as orgs
from dbpilot.orgs.models import User
from dbpilot.projects.models import Project, ProjectMember

ROLE_RANK = {"viewer": 0, "admin": 1, "owner": 2}


@dataclass(frozen=True)
class Principal:
    project: Project
    user: User
    role: str  # viewer, admin, owner
    actor: Actor

    @property
    def project_id(self) -> uuid.UUID:
        return self.project.id

    @property
    def org_id(self) -> uuid.UUID:
        return self.project.org_id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def at_least(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]

    @property
    def manages(self) -> bool:
        """Admins and owners see and may use every connection of the project."""
        return self.at_least("admin")

    def scope(self) -> dict[str, uuid.UUID]:
        """Tenant columns for an audit entry written on this principal's behalf."""
        return {"org_id": self.org_id, "project_id": self.project_id}


async def resolve(
    request: Request, container: ContainerDep, session: SessionDep, project_id: uuid.UUID
) -> Principal:
    user = await current_user(request, container, session)
    project = await session.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    org_role = await orgs.role_in(session, user, project.org_id)
    if org_role in ("owner", "admin"):
        role = "owner"
    else:
        member = await session.get(ProjectMember, (project.id, user.id)) if org_role else None
        if member is None:
            raise NotFoundError("Project not found")
        role = member.role
    return Principal(project, user, role, request.state.actor)


def project_role(role: str) -> Callable[..., Coroutine[Any, Any, Principal]]:
    """A dependency: the caller holds at least ``role`` in the path's project. The path
    parameter is declared here, so every project route documents it."""

    async def dependency(
        request: Request,
        container: ContainerDep,
        session: SessionDep,
        project_id: Annotated[uuid.UUID, Path(description="The project")],
    ) -> Principal:
        p = await resolve(request, container, session, project_id)
        if not p.at_least(role):
            raise PermissionDeniedError(
                f"This action needs the {role} role in this project", code="insufficient_role"
            )
        return p

    return dependency


Viewer = Annotated[Principal, Depends(project_role("viewer"))]
Admin = Annotated[Principal, Depends(project_role("admin"))]
Owner = Annotated[Principal, Depends(project_role("owner"))]
