"""Backup schedules, and the worker's side of them.

The worker (``dbpilot worker``) claims due schedules with ``FOR UPDATE SKIP LOCKED`` and
moves ``next_run_at`` forward in the same transaction, so any number of worker replicas run
each due backup once. It then runs the backup with ``backups.service.run_backup`` (the same
code as a manual backup, audited as ``system:scheduler``) and keeps the newest
``retention_count`` scheduled backups of that connection.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import SCHEDULER, record
from dbpilot.backups.models import Backup, BackupSchedule
from dbpilot.backups.schemas import ScheduleIn, ScheduleOut, ScheduleUpdate
from dbpilot.backups.service import sql_only
from dbpilot.connections.access import accessible, visible_clause, visible_ids
from dbpilot.connections.models import Connection
from dbpilot.core.container import Container
from dbpilot.core.errors import ConflictError, NotFoundError
from dbpilot.projects.access import Principal
from dbpilot.projects.models import Project


def to_out(s: BackupSchedule, conn: Connection) -> ScheduleOut:
    return ScheduleOut(
        id=s.id,
        connection_id=s.connection_id,
        connection_name=conn.name,
        interval_minutes=s.interval_minutes,
        retention_count=s.retention_count,
        enabled=s.enabled,
        next_run_at=s.next_run_at,
        last_run_at=s.last_run_at,
        last_status=s.last_status,
        created_by_label=s.created_by_label,
        created_at=s.created_at,
    )


async def list_schedules(
    session: AsyncSession, p: Principal
) -> list[tuple[BackupSchedule, Connection]]:
    ids = await visible_ids(session, p)
    rows = await session.execute(
        select(BackupSchedule, Connection)
        .join(Connection, Connection.id == BackupSchedule.connection_id)
        .where(
            BackupSchedule.project_id == p.project_id,
            visible_clause(BackupSchedule.connection_id, ids),
        )
        .order_by(Connection.name)
    )
    return [(s, c) for s, c in rows.all()]


async def get(
    session: AsyncSession, p: Principal, schedule_id: uuid.UUID
) -> tuple[BackupSchedule, Connection]:
    for s, c in await list_schedules(session, p):
        if s.id == schedule_id:
            return s, c
    raise NotFoundError("Schedule not found")


async def create(
    session: AsyncSession, p: Principal, data: ScheduleIn
) -> tuple[BackupSchedule, Connection]:
    conn = (await accessible(session, p, data.connection_id)).connection
    sql_only(conn)
    if await session.scalar(
        select(BackupSchedule.id).where(BackupSchedule.connection_id == conn.id)
    ):
        raise ConflictError("This connection already has a schedule", code="schedule_exists")
    s = BackupSchedule(
        id=uuid.uuid4(),
        project_id=p.project_id,
        connection_id=conn.id,
        interval_minutes=data.interval_minutes,
        retention_count=data.retention_count,
        enabled=data.enabled,
        next_run_at=datetime.now(UTC) + timedelta(minutes=data.interval_minutes),
        created_by=p.user_id,
        created_by_label=p.actor.label,
    )
    session.add(s)
    record(
        session,
        p.actor,
        "backup_schedule.created",
        target_type="backup_schedule",
        target_id=s.id,
        connection_id=conn.id,
        interval_minutes=s.interval_minutes,
        retention_count=s.retention_count,
        enabled=s.enabled,
        **p.scope(),
    )
    await session.commit()
    await session.refresh(s)
    return s, conn


async def update_schedule(
    session: AsyncSession, p: Principal, s: BackupSchedule, data: ScheduleUpdate
) -> BackupSchedule:
    changes = data.model_dump(exclude_unset=True)
    if changes.get("interval_minutes"):
        s.interval_minutes = changes["interval_minutes"]
        s.next_run_at = datetime.now(UTC) + timedelta(minutes=s.interval_minutes)
    if changes.get("retention_count"):
        s.retention_count = changes["retention_count"]
    if changes.get("enabled") is not None:
        s.enabled = changes["enabled"]
    record(
        session,
        p.actor,
        "backup_schedule.updated",
        target_type="backup_schedule",
        target_id=s.id,
        connection_id=s.connection_id,
        **changes,
        **p.scope(),
    )
    await session.commit()
    await session.refresh(s)
    return s


async def delete(session: AsyncSession, p: Principal, s: BackupSchedule) -> None:
    record(
        session,
        p.actor,
        "backup_schedule.deleted",
        target_type="backup_schedule",
        target_id=s.id,
        connection_id=s.connection_id,
        **p.scope(),
    )
    await session.delete(s)
    await session.commit()


@dataclass
class Due:
    schedule_id: uuid.UUID
    connection: Connection
    org_id: uuid.UUID
    retention_count: int


async def claim_due(session: AsyncSession, limit: int) -> list[Due]:
    """Due schedules, claimed: ``next_run_at`` moves forward in the claiming transaction, so
    another worker skips them (``SKIP LOCKED``) and never runs them twice."""
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(BackupSchedule, Connection, Project.org_id)
            .join(Connection, Connection.id == BackupSchedule.connection_id)
            .join(Project, Project.id == BackupSchedule.project_id)
            .where(BackupSchedule.enabled, BackupSchedule.next_run_at <= now)
            .order_by(BackupSchedule.next_run_at)
            .limit(limit)
            .with_for_update(of=BackupSchedule, skip_locked=True)
        )
    ).all()
    out: list[Due] = []
    for s, conn, org_id in rows:
        s.last_run_at = now
        s.next_run_at = now + timedelta(minutes=s.interval_minutes)
        out.append(Due(s.id, conn, org_id, s.retention_count))
    await session.commit()
    return out


async def finish_run(session: AsyncSession, schedule_id: uuid.UUID, status: str) -> None:
    await session.execute(
        update(BackupSchedule).where(BackupSchedule.id == schedule_id).values(last_status=status)
    )
    await session.commit()


async def apply_retention(container: Container, session: AsyncSession, due: Due) -> int:
    """Delete this schedule's scheduled backups beyond the newest ``retention_count``."""
    doomed = (
        await session.scalars(
            select(Backup)
            .where(Backup.connection_id == due.connection.id, Backup.trigger == "scheduled")
            .order_by(Backup.started_at.desc(), Backup.id.desc())
            .offset(due.retention_count)
        )
    ).all()
    keys = [b.storage_key for b in doomed if b.storage_key]
    for b in doomed:
        record(
            session,
            SCHEDULER,
            "backup.pruned",
            target_type="backup",
            target_id=b.id,
            connection_id=b.connection_id,
            org_id=due.org_id,
            project_id=b.project_id,
            retention_count=due.retention_count,
        )
        await session.delete(b)
    await session.commit()
    for key in keys:
        await container.storage.delete(key)
    return len(doomed)


async def fail_stale(session: AsyncSession, older_than: timedelta) -> int:
    """Backups still running past ``older_than`` belong to a process that stopped."""
    cutoff = datetime.now(UTC) - older_than
    rows = (
        await session.scalars(
            select(Backup)
            .where(Backup.status == "running", Backup.started_at < cutoff)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for b in rows:
        b.status, b.finished_at = "failed", datetime.now(UTC)
        b.error = "The process running this backup stopped before it finished"
        record(
            session,
            SCHEDULER,
            "backup.failed",
            target_type="backup",
            target_id=b.id,
            connection_id=b.connection_id,
            project_id=b.project_id,
            error=b.error,
        )
    await session.commit()
    return len(rows)
