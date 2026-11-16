from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse

from dbpilot.backups import restore, schedules, service
from dbpilot.backups.schemas import (
    BackupCreate,
    BackupOut,
    RestoreIn,
    ScheduleIn,
    ScheduleOut,
    ScheduleUpdate,
)
from dbpilot.confirmations.schemas import ConfirmationCreated
from dbpilot.confirmations.service import to_out as confirmation_out
from dbpilot.connections.access import accessible
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.core.schemas import Page
from dbpilot.projects.access import Admin, Viewer

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["backups"])


@router.get("/backups", response_model=Page[BackupOut], summary="Backups, newest first")
async def list_backups(
    p: Viewer,
    session: SessionDep,
    connection_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> Page[BackupOut]:
    rows, nxt = await service.list_backups(
        session, p, connection_id=connection_id, cursor=cursor, limit=limit
    )
    return Page[BackupOut](items=[service.to_out(b, c) for b, c in rows], next_cursor=nxt)


@router.post(
    "/backups",
    response_model=BackupOut,
    status_code=201,
    summary="Back up a SQL connection now (a logical dump read on a read-only snapshot, "
    "stored and read back to check it)",
)
async def create_backup(
    body: BackupCreate, p: Admin, session: SessionDep, container: ContainerDep
) -> BackupOut:
    access = await accessible(session, p, body.connection_id)
    backup = await service.run_backup(
        container, session, access.connection, p.actor, org_id=p.org_id, trigger="manual"
    )
    return service.to_out(backup, access.connection)


@router.get("/backups/{backup_id}", response_model=BackupOut, summary="One backup")
async def get_backup(backup_id: uuid.UUID, p: Viewer, session: SessionDep) -> BackupOut:
    backup, conn = await service.get(session, p, backup_id)
    return service.to_out(backup, conn)


@router.post(
    "/backups/{backup_id}/verify",
    response_model=BackupOut,
    summary="Read the stored backup back again: checksum, every record, row counts",
)
async def verify_backup(
    backup_id: uuid.UUID, p: Admin, session: SessionDep, container: ContainerDep
) -> BackupOut:
    backup, conn = await service.get(session, p, backup_id)
    return service.to_out(await service.recheck(container, session, p, backup), conn)


@router.delete("/backups/{backup_id}", status_code=204, summary="Delete a backup and its file")
async def delete_backup(
    backup_id: uuid.UUID, p: Admin, session: SessionDep, container: ContainerDep
) -> Response:
    backup, _ = await service.get(session, p, backup_id)
    await service.delete(container, session, p, backup)
    return Response(status_code=204)


@router.get(
    "/backups/{backup_id}/download",
    response_class=StreamingResponse,
    summary="Download the backup file (project admins; audited)",
)
async def download_backup(
    backup_id: uuid.UUID, p: Admin, session: SessionDep, container: ContainerDep
) -> StreamingResponse:
    backup, conn = await service.get(session, p, backup_id)
    chunks = await service.download(container, session, p, backup)
    name = f"{conn.name}-{backup.started_at:%Y%m%d-%H%M%S}.dbpdump.gz".replace(" ", "_")
    return StreamingResponse(
        chunks,
        media_type="application/gzip",
        headers={"content-disposition": f'attachment; filename="{name}"'},
    )


@router.post(
    "/backups/{backup_id}/restore",
    response_model=ConfirmationCreated,
    status_code=201,
    summary="Prepare a restore into an empty database: described and held for a second, "
    "separate confirmation (nothing runs yet)",
)
async def prepare_restore(
    backup_id: uuid.UUID, body: RestoreIn, p: Admin, session: SessionDep, container: ContainerDep
) -> ConfirmationCreated:
    backup, source = await service.get(session, p, backup_id)
    c, token, holder = await restore.prepare(container, session, p, backup, source, body)
    return ConfirmationCreated(**confirmation_out(c, holder).model_dump(), confirm_token=token)


@router.get("/backup-schedules", response_model=list[ScheduleOut], summary="Backup schedules")
async def list_schedules(p: Viewer, session: SessionDep) -> list[ScheduleOut]:
    return [schedules.to_out(s, c) for s, c in await schedules.list_schedules(session, p)]


@router.post(
    "/backup-schedules",
    response_model=ScheduleOut,
    status_code=201,
    summary="Schedule backups of a connection (run by the worker)",
)
async def create_schedule(body: ScheduleIn, p: Admin, session: SessionDep) -> ScheduleOut:
    s, conn = await schedules.create(session, p, body)
    return schedules.to_out(s, conn)


@router.get("/backup-schedules/{schedule_id}", response_model=ScheduleOut, summary="One schedule")
async def get_schedule(schedule_id: uuid.UUID, p: Viewer, session: SessionDep) -> ScheduleOut:
    s, conn = await schedules.get(session, p, schedule_id)
    return schedules.to_out(s, conn)


@router.patch(
    "/backup-schedules/{schedule_id}", response_model=ScheduleOut, summary="Change a schedule"
)
async def update_schedule(
    schedule_id: uuid.UUID, body: ScheduleUpdate, p: Admin, session: SessionDep
) -> ScheduleOut:
    s, conn = await schedules.get(session, p, schedule_id)
    return schedules.to_out(await schedules.update_schedule(session, p, s, body), conn)


@router.delete("/backup-schedules/{schedule_id}", status_code=204, summary="Remove a schedule")
async def delete_schedule(schedule_id: uuid.UUID, p: Admin, session: SessionDep) -> Response:
    s, _ = await schedules.get(session, p, schedule_id)
    await schedules.delete(session, p, s)
    return Response(status_code=204)
