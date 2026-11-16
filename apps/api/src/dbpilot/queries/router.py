from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse

from dbpilot.confirmations.schemas import ConfirmationCreated
from dbpilot.confirmations.service import to_out as confirmation_out
from dbpilot.connections.access import accessible
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.core.schemas import Page
from dbpilot.projects.access import Viewer
from dbpilot.queries import guard, saved, service, writes
from dbpilot.queries.schemas import (
    Formatted,
    MongoQueryIn,
    PreviewIn,
    QueryResult,
    QueryRunOut,
    QuerySource,
    QueryStatus,
    RedisQueryIn,
    RowsPage,
    SavedQueryIn,
    SavedQueryOut,
    SavedQueryUpdate,
    SqlQueryIn,
    SqlTarget,
    Validation,
    WriteIn,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["queries"])


def _result(outcome: service.Outcome, page_size: int) -> QueryResult:
    return QueryResult(
        **service.to_out(outcome.run).model_dump(), page=service.first_page(outcome, page_size)
    )


@router.post(
    "/queries/sql",
    response_model=QueryResult,
    summary="Run a read-only SQL query: guarded, on a read-only session, under the "
    "connection's scan and row limits",
)
async def run_sql(
    body: SqlQueryIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> QueryResult:
    access = await accessible(session, p, body.connection_id)
    outcome = await service.run_sql(container, session, p, access, body.sql)
    return _result(outcome, body.page_size)


@router.post(
    "/queries/mongodb",
    response_model=QueryResult,
    summary="Read a MongoDB collection: find, aggregate (read stages only), count, distinct",
)
async def run_mongo(
    body: MongoQueryIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> QueryResult:
    access = await accessible(session, p, body.connection_id)
    outcome = await service.run_mongo(container, session, p, access, body)
    return _result(outcome, body.page_size)


@router.post(
    "/queries/redis",
    response_model=QueryResult,
    summary="Run a read-only Redis command (GET, HGETALL, SCAN, TYPE, TTL, ...)",
)
async def run_redis(
    body: RedisQueryIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> QueryResult:
    access = await accessible(session, p, body.connection_id)
    outcome = await service.run_redis(container, session, p, access, body)
    return _result(outcome, body.page_size)


async def _dialect(session: SessionDep, p: Viewer, body: SqlTarget) -> str:
    if body.connection_id is not None:
        return (await accessible(session, p, body.connection_id)).connection.kind
    assert body.dialect is not None
    return body.dialect


@router.post(
    "/queries/validate",
    response_model=Validation,
    summary="Run the SQL guard over a statement: is it exactly one read-only query?",
)
async def validate(body: SqlTarget, p: Viewer, session: SessionDep) -> Validation:
    dialect = await _dialect(session, p, body)
    if dialect not in guard.SQLGLOT_DIALECT:
        return Validation(
            valid=False, dialect=dialect, reason="not a SQL connection", code="engine_mismatch"
        )
    try:
        verdict = guard.check(body.sql, dialect)
    except guard.UnsafeSqlError as exc:
        return Validation(valid=False, dialect=dialect, reason=exc.message, code=exc.code)
    return Validation(valid=True, dialect=dialect, sql=verdict.sql, tables=verdict.tables)


@router.post("/queries/format", response_model=Formatted, summary="Reformat SQL (never run)")
async def format_sql(body: SqlTarget, p: Viewer, session: SessionDep) -> Formatted:
    dialect = await _dialect(session, p, body)
    if dialect not in guard.SQLGLOT_DIALECT:
        return Formatted(sql=body.sql, dialect=dialect)
    return Formatted(sql=guard.pretty(body.sql, dialect), dialect=dialect)


@router.get("/queries", response_model=Page[QueryRunOut], summary="Query history (newest first)")
async def list_queries(
    p: Viewer,
    session: SessionDep,
    connection_id: uuid.UUID | None = None,
    status: QueryStatus | None = None,
    source: QuerySource | None = None,
    mine: bool = Query(False, description="Only queries you ran"),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> Page[QueryRunOut]:
    rows, nxt = await service.list_runs(
        session,
        p,
        connection_id=connection_id,
        status=status,
        source=source,
        mine=mine,
        cursor=cursor,
        limit=limit,
    )
    return Page[QueryRunOut](items=[service.to_out(r) for r in rows], next_cursor=nxt)


@router.get("/queries/{query_id}", response_model=QueryRunOut, summary="One query run")
async def get_query(query_id: uuid.UUID, p: Viewer, session: SessionDep) -> QueryRunOut:
    return service.to_out(await service.get_run(session, p, query_id))


@router.get(
    "/queries/{query_id}/rows",
    response_model=RowsPage,
    summary="A page of a query's result (kept for a limited time, then 410)",
)
async def query_rows(
    query_id: uuid.UUID,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> RowsPage:
    run = await service.get_run(session, p, query_id)
    return await service.page(container, run, offset, limit)


@router.get(
    "/queries/{query_id}/export",
    summary="Download a query's result as CSV or JSON",
    response_class=StreamingResponse,
)
async def export_query(
    query_id: uuid.UUID,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
    format: Literal["csv", "json"] = "csv",  # noqa: A002 the public query parameter name
) -> StreamingResponse:
    run = await service.get_run(session, p, query_id)
    rows = service.export(container, run, format)
    first = await anext(rows)  # a missing result fails here, as a 410, before streaming

    async def body() -> object:
        yield first
        async for chunk in rows:
            yield chunk

    media = "text/csv" if format == "csv" else "application/json"
    return StreamingResponse(
        body(),  # type: ignore[arg-type]
        media_type=media,
        headers={"content-disposition": f'attachment; filename="query-{run.id}.{format}"'},
    )


@router.post(
    "/writes",
    response_model=ConfirmationCreated,
    status_code=201,
    summary="Mark a statement as an intentional write: it is described and held for a second, "
    "separate confirmation (nothing runs yet)",
)
async def prepare_write(
    body: WriteIn, p: Viewer, session: SessionDep, container: ContainerDep
) -> ConfirmationCreated:
    access = await accessible(session, p, body.connection_id)
    c, token = await writes.prepare(container, session, p, access, body.sql, note=body.note)
    return ConfirmationCreated(
        **confirmation_out(c, access.connection).model_dump(), confirm_token=token
    )


@router.post(
    "/connections/{connection_id}/preview",
    response_model=QueryResult,
    summary="The first rows of a table, view or collection (or a Redis key group's keys)",
)
async def preview(
    connection_id: uuid.UUID,
    body: PreviewIn,
    p: Viewer,
    session: SessionDep,
    container: ContainerDep,
) -> QueryResult:
    access = await accessible(session, p, connection_id)
    outcome = await service.preview(container, session, p, access, body.object, body.limit)
    return _result(outcome, body.limit)


@router.get("/saved-queries", response_model=list[SavedQueryOut], summary="Saved queries")
async def list_saved(
    p: Viewer, session: SessionDep, connection_id: uuid.UUID | None = None
) -> list[SavedQueryOut]:
    return [saved.to_out(q) for q in await saved.list_saved(session, p, connection_id)]


@router.post(
    "/saved-queries", response_model=SavedQueryOut, status_code=201, summary="Save a query"
)
async def create_saved(body: SavedQueryIn, p: Viewer, session: SessionDep) -> SavedQueryOut:
    return saved.to_out(await saved.create(session, p, body))


@router.get("/saved-queries/{saved_id}", response_model=SavedQueryOut, summary="One saved query")
async def get_saved(saved_id: uuid.UUID, p: Viewer, session: SessionDep) -> SavedQueryOut:
    return saved.to_out(await saved.get(session, p, saved_id))


@router.patch("/saved-queries/{saved_id}", response_model=SavedQueryOut, summary="Change it")
async def update_saved(
    saved_id: uuid.UUID, body: SavedQueryUpdate, p: Viewer, session: SessionDep
) -> SavedQueryOut:
    q = await saved.get(session, p, saved_id)
    return saved.to_out(await saved.update(session, p, q, body))


@router.delete("/saved-queries/{saved_id}", status_code=204, summary="Delete it")
async def delete_saved(saved_id: uuid.UUID, p: Viewer, session: SessionDep) -> Response:
    q = await saved.get(session, p, saved_id)
    await saved.delete(session, p, q)
    return Response(status_code=204)
