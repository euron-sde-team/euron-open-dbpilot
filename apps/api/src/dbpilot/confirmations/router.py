from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from dbpilot.backups import restore
from dbpilot.confirmations import service
from dbpilot.confirmations.schemas import (
    ConfirmationKind,
    ConfirmationOut,
    ConfirmationStatus,
    ConfirmIn,
    ConfirmResult,
)
from dbpilot.connections.access import accessible
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.core.errors import NotFoundError
from dbpilot.core.schemas import Page
from dbpilot.projects.access import Viewer
from dbpilot.queries import writes

router = APIRouter(prefix="/api/v1/projects/{project_id}/confirmations", tags=["confirmations"])


@router.get(
    "",
    response_model=Page[ConfirmationOut],
    summary="Writes, migrations and restores awaiting (or past) their confirmation",
)
async def list_confirmations(
    p: Viewer,
    session: SessionDep,
    status: ConfirmationStatus | None = None,
    kind: ConfirmationKind | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> Page[ConfirmationOut]:
    rows, nxt = await service.list_for(
        session, p, status=status, kind=kind, cursor=cursor, limit=limit
    )
    return Page[ConfirmationOut](
        items=[service.to_out(c, conn) for c, conn in rows], next_cursor=nxt
    )


@router.get("/{confirmation_id}", response_model=ConfirmationOut, summary="One confirmation")
async def get_confirmation(
    confirmation_id: uuid.UUID, p: Viewer, session: SessionDep
) -> ConfirmationOut:
    c, conn = await service.get(session, p, confirmation_id)
    return service.to_out(c, conn)


@router.post(
    "/{confirmation_id}/confirm",
    response_model=ConfirmResult,
    summary="The second, separate step: confirm exactly what was described. The audit entry "
    "is committed before anything reaches the database",
)
async def confirm(
    confirmation_id: uuid.UUID,
    body: ConfirmIn,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
) -> ConfirmResult:
    pending, _ = await service.get(session, p, confirmation_id)
    try:
        access = await accessible(session, p, pending.connection_id)
        can_write = access.can_write and (pending.kind != "restore" or p.manages)
    except NotFoundError:
        can_write = False
    c, conn = await service.begin(
        session, p, confirmation_id, body.confirm_token, body.digest, can_write=can_write
    )
    query_id: uuid.UUID | None = None
    if c.kind in ("write", "migration"):
        run = await writes.execute(container, session, p, c, conn)
        query_id = run.id
    else:
        await restore.execute(container, session, p, c, conn)
    c, conn = await service.get(session, p, confirmation_id)
    return ConfirmResult(confirmation=service.to_out(c, conn), query_id=query_id)


@router.post(
    "/{confirmation_id}/cancel",
    response_model=ConfirmationOut,
    summary="Cancel a pending confirmation (its author or a project admin)",
)
async def cancel(confirmation_id: uuid.UUID, p: Viewer, session: SessionDep) -> ConfirmationOut:
    c = await service.cancel(session, p, confirmation_id)
    _, conn = await service.get(session, p, c.id)
    return service.to_out(c, conn)
