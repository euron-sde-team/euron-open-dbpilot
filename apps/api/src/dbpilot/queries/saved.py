"""Saved queries: a named statement on a connection, visible to whoever can see it."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import record
from dbpilot.connections.access import accessible, visible_clause, visible_ids
from dbpilot.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from dbpilot.projects.access import Principal
from dbpilot.queries.models import SavedQuery
from dbpilot.queries.schemas import SavedQueryIn, SavedQueryOut, SavedQueryUpdate


def to_out(q: SavedQuery) -> SavedQueryOut:
    return SavedQueryOut.model_validate(q)


async def _unique(
    session: AsyncSession, project_id: uuid.UUID, name: str, exclude: uuid.UUID | None
) -> None:
    stmt = select(SavedQuery.id).where(SavedQuery.project_id == project_id, SavedQuery.name == name)
    if exclude:
        stmt = stmt.where(SavedQuery.id != exclude)
    if await session.scalar(stmt):
        raise ConflictError("A saved query with this name exists", code="name_taken")


async def list_saved(
    session: AsyncSession, p: Principal, connection_id: uuid.UUID | None
) -> list[SavedQuery]:
    ids = await visible_ids(session, p)
    stmt = select(SavedQuery).where(
        SavedQuery.project_id == p.project_id, visible_clause(SavedQuery.connection_id, ids)
    )
    if connection_id:
        stmt = stmt.where(SavedQuery.connection_id == connection_id)
    return list((await session.scalars(stmt.order_by(SavedQuery.name, SavedQuery.id))).all())


async def get(session: AsyncSession, p: Principal, saved_id: uuid.UUID) -> SavedQuery:
    ids = await visible_ids(session, p)
    q = await session.scalar(
        select(SavedQuery).where(
            SavedQuery.id == saved_id,
            SavedQuery.project_id == p.project_id,
            visible_clause(SavedQuery.connection_id, ids),
        )
    )
    if q is None:
        raise NotFoundError("Saved query not found")
    return q


async def create(session: AsyncSession, p: Principal, data: SavedQueryIn) -> SavedQuery:
    await accessible(session, p, data.connection_id)
    await _unique(session, p.project_id, data.name.strip(), None)
    q = SavedQuery(
        project_id=p.project_id,
        connection_id=data.connection_id,
        name=data.name.strip(),
        statement=data.statement,
        description=data.description,
        created_by=p.user_id,
    )
    session.add(q)
    await session.flush()
    record(
        session,
        p.actor,
        "saved_query.created",
        target_type="saved_query",
        target_id=q.id,
        connection_id=data.connection_id,
        name=q.name,
        **p.scope(),
    )
    await session.commit()
    await session.refresh(q)
    return q


def _may_change(p: Principal, q: SavedQuery) -> None:
    if q.created_by != p.user_id and not p.manages:
        raise PermissionDeniedError(
            "Only its author or a project admin can change it", code="not_your_query"
        )


async def update(
    session: AsyncSession, p: Principal, q: SavedQuery, data: SavedQueryUpdate
) -> SavedQuery:
    _may_change(p, q)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name"):
        await _unique(session, p.project_id, changes["name"].strip(), q.id)
        q.name = changes["name"].strip()
    if changes.get("statement") is not None:
        q.statement = changes["statement"]
    if changes.get("description") is not None:
        q.description = changes["description"]
    record(
        session,
        p.actor,
        "saved_query.updated",
        target_type="saved_query",
        target_id=q.id,
        connection_id=q.connection_id,
        fields=sorted(changes),
        **p.scope(),
    )
    await session.commit()
    await session.refresh(q)
    return q


async def delete(session: AsyncSession, p: Principal, q: SavedQuery) -> None:
    _may_change(p, q)
    record(
        session,
        p.actor,
        "saved_query.deleted",
        target_type="saved_query",
        target_id=q.id,
        connection_id=q.connection_id,
        name=q.name,
        **p.scope(),
    )
    await session.delete(q)
    await session.commit()
