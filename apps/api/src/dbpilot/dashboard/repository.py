"""The dashboard's read model: aggregate counts over DBPilot's own tables in one round trip.

This is the one place that reads several domains' tables for reporting. It only counts
(every count is served by an index on ``project_id`` or ``connection_id`` plus a time
column), never changes anything, and is called on a cache miss only (``service.py``). There
is no separately maintained rollup table that could drift from the truth (AC12).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.ai.models import AiGeneration
from dbpilot.backups.models import Backup, BackupSchedule
from dbpilot.confirmations.models import Confirmation
from dbpilot.connections.models import Connection
from dbpilot.projects.models import Project
from dbpilot.queries.models import QueryRun


async def totals(
    session: AsyncSession,
    project_ids: list[uuid.UUID],
    connection_ids: list[uuid.UUID] | None,
) -> dict[str, Any]:
    """Counts for these projects, limited to ``connection_ids`` when given (a viewer's)."""
    now = datetime.now(UTC)
    day, week = now - timedelta(days=1), now - timedelta(days=7)

    def scope(model: Any) -> ColumnElement[bool]:
        clause: ColumnElement[bool] = model.project_id.in_(project_ids)
        if connection_ids is not None:
            column = model.id if model is Connection else model.connection_id
            clause = clause & column.in_(connection_ids)
        return clause

    def count(model: Any, *where: Any) -> Any:
        return select(func.count()).select_from(model).where(scope(model), *where).scalar_subquery()

    pending = Confirmation.status == "pending"
    alive = Confirmation.expires_at > now
    row = (
        await session.execute(
            select(
                count(Connection).label("connections"),
                count(QueryRun).label("queries_total"),
                count(QueryRun, QueryRun.created_at >= day).label("queries_24h"),
                count(QueryRun, QueryRun.created_at >= week, QueryRun.status == "refused").label(
                    "queries_refused_7d"
                ),
                count(
                    QueryRun,
                    QueryRun.created_at >= week,
                    QueryRun.status.in_(("failed", "timeout", "scan_limit")),
                ).label("queries_failed_7d"),
                count(
                    QueryRun,
                    QueryRun.created_at >= week,
                    QueryRun.mode == "write",
                    QueryRun.status == "succeeded",
                ).label("writes_executed_7d"),
                count(
                    AiGeneration,
                    AiGeneration.created_at >= week,
                    AiGeneration.kind == "sql",
                    AiGeneration.status == "accepted",
                ).label("ai_accepted_7d"),
                count(
                    AiGeneration,
                    AiGeneration.created_at >= week,
                    AiGeneration.kind == "sql",
                    AiGeneration.status == "refused",
                ).label("ai_refused_7d"),
                count(Backup, Backup.started_at >= week, Backup.status == "succeeded").label(
                    "backups_succeeded_7d"
                ),
                count(Backup, Backup.started_at >= week, Backup.status == "failed").label(
                    "backups_failed_7d"
                ),
                select(func.max(Backup.started_at))
                .where(scope(Backup), Backup.status == "succeeded")
                .scalar_subquery()
                .label("last_backup_at"),
                count(Confirmation, pending, alive, Confirmation.kind == "write").label(
                    "pending_writes"
                ),
                count(Confirmation, pending, alive, Confirmation.kind == "migration").label(
                    "pending_migrations"
                ),
                count(Confirmation, pending, alive, Confirmation.kind == "restore").label(
                    "pending_restores"
                ),
                count(BackupSchedule, BackupSchedule.enabled).label("schedules_enabled"),
            )
        )
    ).one()
    out = dict(row._mapping)
    conn_clause: ColumnElement[bool] = Connection.project_id.in_(project_ids)
    if connection_ids is not None:
        conn_clause = conn_clause & Connection.id.in_(connection_ids)
    by_engine = (
        await session.execute(
            select(Connection.kind, func.count()).where(conn_clause).group_by(Connection.kind)
        )
    ).all()
    out["connections_by_engine"] = {str(k): int(v) for k, v in by_engine}
    return out


async def org_project_ids(session: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    return list((await session.scalars(select(Project.id).where(Project.org_id == org_id))).all())
