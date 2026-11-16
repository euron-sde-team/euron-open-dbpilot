from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter, Query

from dbpilot.ai import service
from dbpilot.ai.schemas import (
    ConnectionIn,
    DiagnoseIn,
    DiagnoseOut,
    ExplainIn,
    ExplainOut,
    GenerateIn,
    GenerationKind,
    GenerationOut,
    GenerationStatus,
    IndexSuggestionOut,
    IndexSuggestionsOut,
    RunGenerationIn,
)
from dbpilot.connections.access import accessible
from dbpilot.connections.models import Connection
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.core.schemas import Page
from dbpilot.projects.access import Viewer
from dbpilot.queries import service as queries
from dbpilot.queries.schemas import QueryResult

router = APIRouter(prefix="/api/v1/projects/{project_id}/ai", tags=["ai"])


@router.post(
    "/sql",
    response_model=GenerationOut,
    status_code=201,
    summary="Write SQL for a question against the connection's real schema. Shown, never run "
    "here; anything but one read-only query naming real tables is refused for good",
)
async def generate_sql(
    body: GenerateIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> GenerationOut:
    access = await accessible(session, p, body.connection_id)
    return service.to_out(await service.generate_sql(container, session, p, access, body.question))


@router.post(
    "/generations/{generation_id}/run",
    response_model=QueryResult,
    summary="Run an accepted generation through the normal read path (a refused one: 409)",
)
async def run_generation(
    generation_id: uuid.UUID,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
    body: RunGenerationIn | None = None,
) -> QueryResult:
    outcome = await service.run_generation(container, session, p, generation_id)
    size = body.page_size if body else 100
    return QueryResult(
        **queries.to_out(outcome.run).model_dump(), page=queries.first_page(outcome, size)
    )


@router.post(
    "/explain",
    response_model=ExplainOut,
    summary="Explain any statement in plain language (never executed)",
)
async def explain(
    body: ExplainIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> ExplainOut:
    conn: Connection | None = None
    dialect: str = body.dialect or ""
    if body.connection_id is not None:
        conn = (await accessible(session, p, body.connection_id)).connection
        dialect = conn.kind
    g, analysis, source = await service.explain(
        container, session, p, body.sql, dialect, conn, use_model=body.use_model
    )
    return ExplainOut(
        generation_id=g.id,
        explanation=g.explanation,
        source=source,
        model=g.model,
        analysis=analysis,
    )


@router.post(
    "/index-suggestions",
    response_model=IndexSuggestionsOut,
    summary="Missing-index suggestions from the real schema and this connection's queries",
)
async def index_suggestions(
    body: ConnectionIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> IndexSuggestionsOut:
    access = await accessible(session, p, body.connection_id)
    g, snapshot, found, analyzed = await service.index_suggestions(container, session, p, access)
    return IndexSuggestionsOut(
        generation_id=g.id,
        snapshot_id=snapshot.id,
        analyzed_queries=analyzed,
        suggestions=[IndexSuggestionOut.model_validate(asdict(s)) for s in found],
    )


@router.post(
    "/diagnose",
    response_model=DiagnoseOut,
    summary="Why the database refused a statement, and the closest real names",
)
async def diagnose(
    body: DiagnoseIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> DiagnoseOut:
    access = await accessible(session, p, body.connection_id)
    g, found, narrative = await service.diagnose(
        container, session, p, access, body.sql, body.error
    )
    return DiagnoseOut(
        generation_id=g.id,
        category=found.category,
        explanation=found.explanation,
        suggestion=found.suggestion,
        similar=found.similar,
        model_explanation=narrative,
    )


@router.get(
    "/generations",
    response_model=Page[GenerationOut],
    summary="Assistant history, refused included",
)
async def list_generations(
    p: Viewer,
    session: SessionDep,
    connection_id: uuid.UUID | None = None,
    kind: GenerationKind | None = None,
    status: GenerationStatus | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> Page[GenerationOut]:
    rows, nxt = await service.list_generations(
        session,
        p,
        connection_id=connection_id,
        kind=kind,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return Page[GenerationOut](items=[service.to_out(g) for g in rows], next_cursor=nxt)


@router.get("/generations/{generation_id}", response_model=GenerationOut, summary="One output")
async def get_generation(generation_id: uuid.UUID, p: Viewer, session: SessionDep) -> GenerationOut:
    return service.to_out(await service.get(session, p, generation_id))
