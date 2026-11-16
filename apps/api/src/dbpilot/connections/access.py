"""Connection-level access (acceptance criterion AC9).

Project admins and owners see and may use every connection of the project. A viewer sees
a connection only through a grant (to them, or to every project member). A connection
someone has no grant on answers 404 on every route, exactly like one that does not exist,
and everything derived from it (schema snapshots, query history, AI generations, backups,
comparisons, confirmations, health) is filtered the same way. ``accessible`` is the one
resolver every connection-scoped route goes through.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import ColumnElement, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.connections.models import Connection, ConnectionGrant
from dbpilot.core.errors import NotFoundError, PermissionDeniedError
from dbpilot.projects.access import Principal


@dataclass(frozen=True)
class ConnectionAccess:
    connection: Connection
    can_write: bool
    via: Literal["role", "grant", "all_members"]


def _grants_for(user_id: uuid.UUID) -> ColumnElement[bool]:
    return or_(ConnectionGrant.user_id == user_id, ConnectionGrant.user_id.is_(None))


async def accessible(
    session: AsyncSession, p: Principal, connection_id: uuid.UUID
) -> ConnectionAccess:
    """The connection and the caller's access to it, or 404 (never 403) without a grant."""
    conn = await session.scalar(
        select(Connection).where(
            Connection.id == connection_id, Connection.project_id == p.project_id
        )
    )
    if conn is None:
        raise NotFoundError("Connection not found")
    if p.manages:
        return ConnectionAccess(conn, True, "role")
    grants = (
        await session.scalars(
            select(ConnectionGrant).where(
                ConnectionGrant.connection_id == conn.id, _grants_for(p.user_id)
            )
        )
    ).all()
    if not grants:
        raise NotFoundError("Connection not found")
    personal = any(g.user_id is not None for g in grants)
    return ConnectionAccess(
        conn, any(g.can_write for g in grants), "grant" if personal else "all_members"
    )


def require_write(access: ConnectionAccess) -> None:
    if not access.can_write:
        raise PermissionDeniedError(
            "You may use this connection read-only. A project admin can grant writes",
            code="write_not_granted",
        )


async def visible_ids(session: AsyncSession, p: Principal) -> list[uuid.UUID] | None:
    """Connection ids the caller may see; None means every connection of the project."""
    if p.manages:
        return None
    return list(
        (
            await session.scalars(
                select(ConnectionGrant.connection_id)
                .where(ConnectionGrant.project_id == p.project_id, _grants_for(p.user_id))
                .distinct()
            )
        ).all()
    )


def visible_clause(column: Any, ids: list[uuid.UUID] | None) -> ColumnElement[bool]:
    """A filter keeping rows whose connection the caller may see."""
    return true() if ids is None else column.in_(ids)


async def access_map(
    session: AsyncSession, p: Principal, connections: list[Connection]
) -> dict[uuid.UUID, ConnectionAccess]:
    """The caller's access to each visible connection (one grouped query)."""
    if p.manages:
        return {c.id: ConnectionAccess(c, True, "role") for c in connections}
    ids = [c.id for c in connections]
    rows = (
        await session.execute(
            select(
                ConnectionGrant.connection_id, ConnectionGrant.user_id, ConnectionGrant.can_write
            ).where(ConnectionGrant.connection_id.in_(ids), _grants_for(p.user_id))
        )
    ).all()
    out: dict[uuid.UUID, ConnectionAccess] = {}
    by_id = {c.id: c for c in connections}
    for cid, user_id, can_write in rows:
        prev = out.get(cid)
        via: Literal["role", "grant", "all_members"] = "grant" if user_id else "all_members"
        if prev is not None:
            via = "grant" if prev.via == "grant" else via
            can_write = can_write or prev.can_write
        out[cid] = ConnectionAccess(by_id[cid], bool(can_write), via)
    return out


async def visible_counts(
    session: AsyncSession,
    user_id: uuid.UUID,
    project_ids: list[uuid.UUID],
    managed: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Per project, how many connections ``user_id`` can see (two grouped queries)."""
    out: dict[uuid.UUID, int] = {}
    if managed:
        out.update(
            {
                k: int(v)
                for k, v in (
                    await session.execute(
                        select(Connection.project_id, func.count())
                        .where(Connection.project_id.in_(managed))
                        .group_by(Connection.project_id)
                    )
                ).all()
            }
        )
    others = [pid for pid in project_ids if pid not in set(managed)]
    if others:
        out.update(
            {
                k: int(v)
                for k, v in (
                    await session.execute(
                        select(
                            ConnectionGrant.project_id,
                            func.count(func.distinct(ConnectionGrant.connection_id)),
                        )
                        .where(ConnectionGrant.project_id.in_(others), _grants_for(user_id))
                        .group_by(ConnectionGrant.project_id)
                    )
                ).all()
            }
        )
    return out
