from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from dbpilot.comparisons import service
from dbpilot.comparisons.schemas import ComparisonIn, ComparisonOut, RunComparisonIn
from dbpilot.confirmations.schemas import ConfirmationCreated
from dbpilot.confirmations.service import to_out as confirmation_out
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.core.schemas import Page
from dbpilot.projects.access import Viewer

router = APIRouter(prefix="/api/v1/projects/{project_id}/comparisons", tags=["comparisons"])


@router.post(
    "",
    response_model=ComparisonOut,
    status_code=201,
    summary="Compare two schema snapshots (or two connections' latest) and generate the "
    "migration script. Nothing runs",
)
async def compare(
    body: ComparisonIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> ComparisonOut:
    return await service.create(container, session, p, body)


@router.get("", response_model=Page[ComparisonOut], summary="Recent comparisons")
async def list_comparisons(
    p: Viewer,
    session: SessionDep,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> Page[ComparisonOut]:
    rows, nxt = await service.list_comparisons(session, p, cursor, limit)
    return Page[ComparisonOut](items=[service.to_out(c) for c in rows], next_cursor=nxt)


@router.get("/{comparison_id}", response_model=ComparisonOut, summary="One comparison")
async def get_comparison(comparison_id: uuid.UUID, p: Viewer, session: SessionDep) -> ComparisonOut:
    return service.to_out(await service.get(session, p, comparison_id))


@router.post(
    "/{comparison_id}/run",
    response_model=ConfirmationCreated,
    status_code=201,
    summary="Prepare the migration script to run on the 'from' connection through the "
    "confirmed-write path (nothing runs until it is confirmed)",
)
async def run_comparison(
    comparison_id: uuid.UUID,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
    body: RunComparisonIn | None = None,
) -> ConfirmationCreated:
    c = await service.get(session, p, comparison_id)
    confirmation, token, access = await service.prepare_run(
        container, session, p, c, body.note if body else ""
    )
    return ConfirmationCreated(
        **confirmation_out(confirmation, access.connection).model_dump(), confirm_token=token
    )
