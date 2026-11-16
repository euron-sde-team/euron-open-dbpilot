"""Connections: saved once with a sealed password, tested, granted, and opened only by the
gateway (read-only for every read)."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import record
from dbpilot.connections import gateway
from dbpilot.connections.access import ConnectionAccess, access_map, visible_ids
from dbpilot.connections.connectors.discovery import schemas_of
from dbpilot.connections.connectors.mongo import MongoReader
from dbpilot.connections.connectors.redis_source import RedisReader
from dbpilot.connections.connectors.sql import confirm_read_only, server_version
from dbpilot.connections.connectors.targets import DEFAULT_PORTS
from dbpilot.connections.models import Connection, ConnectionGrant
from dbpilot.connections.schemas import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    GrantIn,
    GrantOut,
    Limits,
    MyAccess,
    TestResult,
)
from dbpilot.core import kv, metrics
from dbpilot.core.container import Container
from dbpilot.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnprocessableError,
)
from dbpilot.orgs.models import User
from dbpilot.projects.access import Principal
from dbpilot.projects.models import ProjectMember

log = logging.getLogger("dbpilot.connections")
_SERVER_FIELDS = (
    "host",
    "port",
    "database",
    "username",
    "ssl_mode",
    "default_schema",
    "auth_source",
)


def _config(data: ConnectionCreate) -> dict[str, Any]:
    ssl_default = "prefer" if data.kind == "postgresql" else "disable"
    return {
        "host": data.host,
        "port": data.port or DEFAULT_PORTS[data.kind],
        "database": data.database,
        "username": data.username,
        "ssl_mode": data.ssl_mode or ssl_default,
        "default_schema": data.default_schema,
        "auth_source": data.auth_source if data.kind == "mongodb" else None,
    }


def to_out(
    c: Connection, access: ConnectionAccess, snapshot: tuple[int, datetime] | None = None
) -> ConnectionOut:
    cfg = c.config or {}
    return ConnectionOut(
        id=c.id,
        name=c.name,
        kind=c.kind,
        description=c.description,
        host=cfg.get("host"),
        port=cfg.get("port"),
        database=cfg.get("database"),
        username=cfg.get("username") or None,
        ssl_mode=cfg.get("ssl_mode"),
        default_schema=cfg.get("default_schema"),
        auth_source=cfg.get("auth_source"),
        has_password=c.secret_sealed is not None,
        file_name=c.file_name,
        file_size=c.file_size,
        max_rows=c.max_rows,
        max_scan_rows=c.max_scan_rows,
        timeout_s=c.timeout_s,
        sample_values=c.sample_values,
        last_tested_at=c.last_tested_at,
        last_test_ok=c.last_test_ok,
        last_test_message=c.last_test_message,
        server_version=c.server_version,
        schema_objects=snapshot[0] if snapshot else None,
        schema_captured_at=snapshot[1] if snapshot else None,
        access=MyAccess(can_write=access.can_write, via=access.via),
        created_at=c.created_at,
    )


async def outs(session: AsyncSession, p: Principal, rows: list[Connection]) -> list[ConnectionOut]:
    """Connections with the caller's access and their latest snapshot (grouped queries)."""
    from dbpilot.schema import service as schemas

    if not rows:
        return []
    access = await access_map(session, p, rows)
    latest = await schemas.latest_summaries(session, [c.id for c in rows])
    return [to_out(c, access[c.id], latest.get(c.id)) for c in rows if c.id in access]


async def list_connections(session: AsyncSession, p: Principal) -> list[ConnectionOut]:
    ids = await visible_ids(session, p)
    stmt = select(Connection).where(Connection.project_id == p.project_id)
    if ids is not None:
        stmt = stmt.where(Connection.id.in_(ids))
    rows = list((await session.scalars(stmt.order_by(Connection.name, Connection.id))).all())
    return await outs(session, p, rows)


async def _unique_name(
    session: AsyncSession, project_id: uuid.UUID, name: str, exclude: uuid.UUID | None = None
) -> None:
    stmt = select(Connection.id).where(Connection.project_id == project_id, Connection.name == name)
    if exclude:
        stmt = stmt.where(Connection.id != exclude)
    if await session.scalar(stmt):
        raise ConflictError("A connection with this name exists in the project", code="name_taken")


def _limits(container: Container, data: Limits) -> dict[str, int]:
    s = container.settings
    return {
        "max_rows": min(data.max_rows, s.max_rows_ceiling),
        "max_scan_rows": data.max_scan_rows,
        "timeout_s": min(data.timeout_s, s.max_timeout_ceiling_s),
    }


async def create_server(
    container: Container, session: AsyncSession, p: Principal, data: ConnectionCreate
) -> Connection:
    await _unique_name(session, p.project_id, data.name.strip())
    conn = Connection(
        id=uuid.uuid4(),
        project_id=p.project_id,
        name=data.name.strip(),
        kind=data.kind,
        config=_config(data),
        sample_values=data.sample_values,
        description=data.description,
        created_by=p.user_id,
        **_limits(container, data),
    )
    if data.password:
        conn.secret_sealed = await container.sealer.seal(
            data.password, gateway.secret_context(conn)
        )
    session.add(conn)
    record(
        session,
        p.actor,
        "connection.created",
        target_type="connection",
        target_id=conn.id,
        connection_id=conn.id,
        name=conn.name,
        engine=conn.kind,
        host=data.host,
        **p.scope(),
    )
    await session.commit()
    await session.refresh(conn)
    return conn


async def store_upload(container: Container, upload: UploadFile, key: str) -> int:
    """Stream an upload to a temp file, check its size and that it is SQLite, store it."""
    limit = container.settings.max_upload_bytes
    size = 0
    work = Path(container.settings.work_dir)
    await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=work, delete=True) as tmp:
        head = b""
        while chunk := await upload.read(1024 * 1024):
            if not head:
                head = chunk[:64]
            size += len(chunk)
            if size > limit:
                raise AppError(f"The file is larger than {limit:,} bytes", code="file_too_large")
            await asyncio.to_thread(tmp.write, chunk)
        await asyncio.to_thread(tmp.flush)
        if size == 0:
            raise AppError("The file is empty", code="file_empty")
        if not head.startswith(gateway.SQLITE_MAGIC):
            raise AppError("This is not a SQLite database file", code="not_sqlite")
        await container.storage.put_file(key, Path(tmp.name), "application/vnd.sqlite3")
    return size


async def create_sqlite(
    container: Container,
    session: AsyncSession,
    p: Principal,
    name: str,
    upload: UploadFile,
    limits: Limits,
    *,
    sample_values: bool = False,
    description: str = "",
) -> Connection:
    """A SQLite database file, kept in object storage and read from private copies."""
    await _unique_name(session, p.project_id, name.strip())
    conn_id = uuid.uuid4()
    key = f"projects/{p.project_id}/connections/{conn_id}/{uuid.uuid4().hex}.sqlite"
    size = await store_upload(container, upload, key)
    conn = Connection(
        id=conn_id,
        project_id=p.project_id,
        name=name.strip(),
        kind="sqlite",
        config={},
        file_key=key,
        file_name=(upload.filename or "database.sqlite")[:300],
        file_size=size,
        sample_values=sample_values,
        description=description,
        created_by=p.user_id,
        **_limits(container, limits),
    )
    session.add(conn)
    record(
        session,
        p.actor,
        "connection.created",
        target_type="connection",
        target_id=conn.id,
        connection_id=conn.id,
        name=conn.name,
        engine="sqlite",
        file_size=size,
        **p.scope(),
    )
    await session.commit()
    await session.refresh(conn)
    return conn


_TLS_STRENGTH = {"disable": 0, "prefer": 1, "require": 2, "verify-full": 3}


def _needs_password_again(conn: Connection, changes: dict[str, Any]) -> bool:
    """Whether ``changes`` would send the stored password somewhere it was not saved for:
    another host, port or user, or over weaker TLS than it was saved with."""
    if not conn.secret_sealed:
        return False
    cfg = conn.config or {}
    moved = any(
        changes.get(f) is not None and changes[f] != cfg.get(f)
        for f in ("host", "port", "username")
    )
    default_tls = "prefer" if conn.kind == "postgresql" else "disable"
    new_tls = changes.get("ssl_mode")
    weaker = new_tls is not None and _TLS_STRENGTH.get(str(new_tls), 0) < _TLS_STRENGTH.get(
        str(cfg.get("ssl_mode") or default_tls), 0
    )
    return moved or weaker


async def update(
    container: Container,
    session: AsyncSession,
    p: Principal,
    conn: Connection,
    data: ConnectionUpdate,
) -> Connection:
    changes = data.model_dump(exclude_unset=True)
    fields = sorted(changes)
    password = changes.pop("password", None)
    if password is None and _needs_password_again(conn, changes):
        raise UnprocessableError(
            "Enter the password again to change the host, port, user or to weaken TLS: "
            "DBPilot never sends a stored password to a server it was not saved for",
            code="password_required",
        )
    if changes.get("name"):
        await _unique_name(session, p.project_id, changes["name"].strip(), exclude=conn.id)
        conn.name = changes.pop("name").strip()
    s = container.settings
    if changes.get("max_rows"):
        conn.max_rows = min(changes["max_rows"], s.max_rows_ceiling)
    if changes.get("max_scan_rows"):
        conn.max_scan_rows = changes["max_scan_rows"]
    if changes.get("timeout_s"):
        conn.timeout_s = min(changes["timeout_s"], s.max_timeout_ceiling_s)
    if changes.get("sample_values") is not None:
        conn.sample_values = changes["sample_values"]
    if changes.get("description") is not None:
        conn.description = changes["description"]
    if conn.kind != "sqlite":
        cfg = dict(conn.config or {})
        for name in _SERVER_FIELDS:
            if name in changes and (changes[name] is not None or name == "default_schema"):
                cfg[name] = changes[name]
        conn.config = cfg
    if password is not None:
        conn.secret_sealed = (
            await container.sealer.seal(password, gateway.secret_context(conn))
            if password
            else None
        )
    record(
        session,
        p.actor,
        "connection.updated",
        target_type="connection",
        target_id=conn.id,
        connection_id=conn.id,
        fields=fields,
        **p.scope(),
    )
    await session.commit()
    await session.refresh(conn)
    await kv.cache_delete(container.kv, f"health:{conn.id}", "health")
    return conn


async def delete(
    container: Container, session: AsyncSession, p: Principal, conn: Connection
) -> None:
    """Remove the connection with its grants, snapshots, history and backups (the audit log
    keeps every entry). Stored files (backups, the SQLite file) go after the commit."""
    from dbpilot.backups import service as backups

    keys = await backups.storage_keys_for(session, conn.id)
    if conn.file_key:
        keys.append(conn.file_key)
    record(
        session,
        p.actor,
        "connection.deleted",
        target_type="connection",
        target_id=conn.id,
        connection_id=conn.id,
        name=conn.name,
        engine=conn.kind,
        **p.scope(),
    )
    await session.delete(conn)
    await session.commit()
    for key in keys:
        await container.storage.delete(key)
    await kv.cache_delete(container.kv, f"health:{conn.id}", "health")


# ─── Testing ─────────────────────────────────────────────────────────────────


@dataclass
class _Probe:
    version: str
    objects: int
    read_only: bool | None
    detail: str


def _probe(handle: gateway.Handle) -> _Probe:
    if isinstance(handle, MongoReader):
        handle.ping()
        return _Probe(
            handle.server_version(),
            len(handle.collection_names()),
            None,
            "MongoDB has no read-only session; DBPilot only sends reads (allowlist)",
        )
    if isinstance(handle, RedisReader):
        handle.ping()
        return _Probe(
            handle.server_version(),
            handle.dbsize(),
            None,
            "Redis has no read-only session; DBPilot only runs read commands (allowlist)",
        )
    kind = handle.dialect.name
    inspector = sa.inspect(handle)
    names = 0
    for s in schemas_of(handle, None):
        names += len(inspector.get_table_names(schema=s)) + len(inspector.get_view_names(schema=s))
    if kind == "mssql":
        return _Probe(
            server_version(handle),
            names,
            False,
            "SQL Server has no read-only session; reads run in rollback-only transactions",
        )
    read_only = confirm_read_only(handle)
    return _Probe(
        server_version(handle),
        names,
        read_only,
        "The database confirmed the session is read-only"
        if read_only
        else "The database did not confirm a read-only session",
    )


def friendly(message: str) -> str:
    """Driver errors, rewritten to say what to check."""
    m = message.lower()
    if "showplan permission denied" in m or "lacking privileges for underlying table" in m:
        return (
            "DBPilot plans every query before it runs it, to hold it to the connection's scan "
            "limit, and this login may not see the plan. Grant it SHOWPLAN (SQL Server), or "
            "SHOW VIEW on the views it reads (MySQL)"
        )
    if any(
        s in m
        for s in (
            "password authentication failed",
            "access denied",
            "login failed",
            "authentication failed",
            "wrongpass",
            "invalid password",
            "noauth",
        )
    ):
        return "The username or password was refused. Check them, and that the user may log in"
    if ("does not exist" in m and "database" in m) or "unknown database" in m:
        return "The database name was not found on the server"
    if any(
        s in m
        for s in (
            "could not translate host",
            "name or service not known",
            "nodename nor servname",
            "name resolution",
            "getaddrinfo failed",
        )
    ):
        return "The host name could not be resolved. Check the spelling, or use an IP address"
    if any(
        s in m
        for s in (
            "connection refused",
            "can't connect",
            "unable to connect",
            "error 111",
            "adaptive server is unavailable",
        )
    ):
        return (
            "Nothing answered on that host and port. Check the port, the firewall and that "
            "the database is running"
        )
    if "timeout" in m or "timed out" in m:
        return "The connection timed out. Check that the database is reachable from DBPilot"
    if "ssl" in m or "tls" in m:
        return f"The TLS handshake failed ({message[:160]}). Try another SSL mode"
    return message


async def test(
    container: Container,
    session: AsyncSession,
    p: Principal,
    conn: Connection,
    *,
    password: str | None = None,
    timeout_s: int = 20,
) -> TestResult:
    """Connect, read the server version, count the visible objects and confirm the session
    is read-only. Rate limited per project: a test is a login attempt against a database."""
    decision = await kv.limit(
        container.kv, f"conntest:{p.project_id}", container.settings.connection_test_rpm
    )
    if not decision.allowed:
        raise RateLimitedError(
            "Too many connection tests in this project. Wait a minute and try again",
            headers={"retry-after": str(decision.retry_after_s)},
        )
    started = time.perf_counter()
    saved = gateway.is_saved(conn)
    outcome: list[TestResult] = []

    def on_done(result: _Probe | None, error: BaseException | None) -> dict[str, Any]:
        if result is not None:
            res = TestResult(
                ok=True,
                message=f"Connected. {result.objects} objects visible. {result.detail}",
                server_version=result.version,
                objects=result.objects,
                latency_ms=_ms(started),
                read_only=result.read_only,
                read_only_detail=result.detail,
            )
        elif isinstance(error, TimeoutError):
            res = TestResult(
                ok=False, message="The database did not answer in time", latency_ms=_ms(started)
            )
        elif isinstance(error, AppError):
            res = TestResult(ok=False, message=error.message, latency_ms=_ms(started))
        else:
            text = gateway.error_text(error) if error else "unknown error"
            res = TestResult(ok=False, message=friendly(text), latency_ms=_ms(started))
        outcome.append(res)
        if saved:
            conn.last_tested_at = datetime.now(UTC)
            conn.last_test_ok = res.ok
            conn.last_test_message = res.message
            if res.server_version:
                conn.server_version = res.server_version
        return {"ok": res.ok, "read_only": res.read_only}

    try:
        await gateway.read(
            container,
            session,
            conn,
            p.actor,
            action="connection.tested",
            work=_probe,
            org_id=p.org_id,
            on_done=on_done,
            timeout_s=timeout_s,
            password=password,
        )
    except Exception as exc:  # the outcome, with the database's own message, is recorded
        log.info(
            "connection test failed",
            extra={"connection": conn.name, "error": gateway.error_text(exc)},
        )
    result = (
        outcome[0]
        if outcome
        else TestResult(ok=False, message="The test did not run", latency_ms=_ms(started))
    )
    metrics.CONNECTION_TESTS.labels(conn.kind, "ok" if result.ok else "failed").inc()
    if saved:
        await kv.cache_delete(container.kv, f"health:{conn.id}", "health")
    return result


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def unsaved(project_id: uuid.UUID, data: ConnectionCreate) -> Connection:
    """A connection that is only tested, never stored (no row, no sealed password)."""
    return Connection(
        id=uuid.uuid4(),
        project_id=project_id,
        name=data.name,
        kind=data.kind,
        config=_config(data),
        timeout_s=min(data.timeout_s, 20),
        max_rows=data.max_rows,
        max_scan_rows=data.max_scan_rows,
    )


# ─── Grants ──────────────────────────────────────────────────────────────────


async def grants(session: AsyncSession, conn: Connection) -> list[GrantOut]:
    rows = (
        await session.execute(
            select(ConnectionGrant, User)
            .outerjoin(User, User.id == ConnectionGrant.user_id)
            .where(ConnectionGrant.connection_id == conn.id)
            .order_by(ConnectionGrant.created_at, ConnectionGrant.id)
        )
    ).all()
    return [
        GrantOut(
            id=g.id,
            connection_id=g.connection_id,
            user_id=g.user_id,
            email=u.email if u else None,
            name=u.name if u else None,
            all_members=g.user_id is None,
            can_write=g.can_write,
            created_at=g.created_at,
        )
        for g, u in rows
    ]


async def put_grant(
    session: AsyncSession, p: Principal, conn: Connection, data: GrantIn
) -> list[GrantOut]:
    """Grant (or change) access for one project member, or for every member."""
    if data.user_id is not None and not await session.get(
        ProjectMember, (p.project_id, data.user_id)
    ):
        raise NotFoundError("That person is not a member of this project", code="not_a_member")
    clause = (
        ConnectionGrant.user_id.is_(None)
        if data.user_id is None
        else ConnectionGrant.user_id == data.user_id
    )
    grant = await session.scalar(
        select(ConnectionGrant).where(ConnectionGrant.connection_id == conn.id, clause)
    )
    if grant is None:
        grant = ConnectionGrant(
            project_id=p.project_id,
            connection_id=conn.id,
            user_id=data.user_id,
            created_by=p.user_id,
        )
        session.add(grant)
    grant.can_write = data.can_write
    record(
        session,
        p.actor,
        "connection.grant_changed",
        target_type="connection",
        target_id=conn.id,
        connection_id=conn.id,
        grantee=str(data.user_id) if data.user_id else "all_members",
        can_write=data.can_write,
        **p.scope(),
    )
    await session.commit()
    return await grants(session, conn)


async def delete_grant(
    session: AsyncSession, p: Principal, conn: Connection, grant_id: uuid.UUID
) -> None:
    grant = await session.scalar(
        select(ConnectionGrant).where(
            ConnectionGrant.id == grant_id, ConnectionGrant.connection_id == conn.id
        )
    )
    if grant is None:
        raise NotFoundError("Grant not found")
    record(
        session,
        p.actor,
        "connection.grant_revoked",
        target_type="connection",
        target_id=conn.id,
        connection_id=conn.id,
        grantee=str(grant.user_id) if grant.user_id else "all_members",
        **p.scope(),
    )
    await session.delete(grant)
    await session.commit()
