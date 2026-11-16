from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, Response, UploadFile

from dbpilot.connections import service
from dbpilot.connections.access import accessible
from dbpilot.connections.schemas import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    GrantIn,
    GrantOut,
    Limits,
    TestResult,
)
from dbpilot.core.deps import ContainerDep, SessionDep
from dbpilot.projects.access import Admin, Viewer

router = APIRouter(prefix="/api/v1/projects/{project_id}/connections", tags=["connections"])


async def _one(session: SessionDep, p: Viewer, connection_id: uuid.UUID) -> ConnectionOut:
    access = await accessible(session, p, connection_id)
    return (await service.outs(session, p, [access.connection]))[0]


@router.get(
    "",
    response_model=list[ConnectionOut],
    summary="Connections you can see (admins: all; viewers: the ones granted to them)",
)
async def list_connections(p: Viewer, session: SessionDep) -> list[ConnectionOut]:
    return await service.list_connections(session, p)


@router.post(
    "",
    response_model=ConnectionOut,
    status_code=201,
    summary="Add a PostgreSQL, MySQL, SQL Server, MongoDB or Redis database (the password is "
    "sealed and never returned)",
)
async def create_connection(
    body: ConnectionCreate, p: Admin, session: SessionDep, container: ContainerDep
) -> ConnectionOut:
    conn = await service.create_server(container, session, p, body)
    return await _one(session, p, conn.id)


@router.post(
    "/sqlite",
    response_model=ConnectionOut,
    status_code=201,
    summary="Upload a SQLite database file",
)
async def create_sqlite(
    p: Admin,
    session: SessionDep,
    container: ContainerDep,
    name: str = Form(min_length=1, max_length=120),
    file: UploadFile = File(...),
    max_rows: int = Form(1000, ge=1, le=100_000),
    max_scan_rows: int = Form(10_000_000, ge=1),
    timeout_s: int = Form(30, ge=1, le=600),
    sample_values: bool = Form(False),
    description: str = Form("", max_length=2000),
) -> ConnectionOut:
    limits = Limits(max_rows=max_rows, max_scan_rows=max_scan_rows, timeout_s=timeout_s)
    conn = await service.create_sqlite(
        container,
        session,
        p,
        name,
        file,
        limits,
        sample_values=sample_values,
        description=description,
    )
    return await _one(session, p, conn.id)


@router.post(
    "/test",
    response_model=TestResult,
    summary="Test a connection before saving it: latency, server version, read-only session",
)
async def test_unsaved(
    body: ConnectionCreate, p: Admin, session: SessionDep, container: ContainerDep
) -> TestResult:
    return await service.test(
        container, session, p, service.unsaved(p.project_id, body), password=body.password or ""
    )


@router.get("/{connection_id}", response_model=ConnectionOut, summary="One connection")
async def get_connection(connection_id: uuid.UUID, p: Viewer, session: SessionDep) -> ConnectionOut:
    return await _one(session, p, connection_id)


@router.patch("/{connection_id}", response_model=ConnectionOut, summary="Change a connection")
async def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
    p: Admin,
    session: SessionDep,
    container: ContainerDep,
) -> ConnectionOut:
    access = await accessible(session, p, connection_id)
    await service.update(container, session, p, access.connection, body)
    return await _one(session, p, connection_id)


@router.delete(
    "/{connection_id}",
    status_code=204,
    summary="Delete a connection with its grants, snapshots, history and backups",
)
async def delete_connection(
    connection_id: uuid.UUID, p: Admin, session: SessionDep, container: ContainerDep
) -> Response:
    access = await accessible(session, p, connection_id)
    await service.delete(container, session, p, access.connection)
    return Response(status_code=204)


@router.post(
    "/{connection_id}/test",
    response_model=TestResult,
    summary="Test a saved connection: latency, server version, read-only session",
)
async def test_connection(
    connection_id: uuid.UUID, p: Viewer, session: SessionDep, container: ContainerDep
) -> TestResult:
    access = await accessible(session, p, connection_id)
    return await service.test(container, session, p, access.connection)


@router.get(
    "/{connection_id}/grants", response_model=list[GrantOut], summary="Who may use the connection"
)
async def list_grants(connection_id: uuid.UUID, p: Admin, session: SessionDep) -> list[GrantOut]:
    access = await accessible(session, p, connection_id)
    return await service.grants(session, access.connection)


@router.put(
    "/{connection_id}/grants",
    response_model=list[GrantOut],
    summary="Grant one member (or every member, user_id null) access, optionally with writes",
)
async def put_grant(
    connection_id: uuid.UUID, body: GrantIn, p: Admin, session: SessionDep
) -> list[GrantOut]:
    access = await accessible(session, p, connection_id)
    return await service.put_grant(session, p, access.connection, body)


@router.delete("/{connection_id}/grants/{grant_id}", status_code=204, summary="Revoke a grant")
async def delete_grant(
    connection_id: uuid.UUID, grant_id: uuid.UUID, p: Admin, session: SessionDep
) -> Response:
    access = await accessible(session, p, connection_id)
    await service.delete_grant(session, p, access.connection, grant_id)
    return Response(status_code=204)
