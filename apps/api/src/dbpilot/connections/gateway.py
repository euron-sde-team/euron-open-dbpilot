"""The one door to connected databases (acceptance criterion AC10).

Every operation that reaches a connected database, whatever asked for it (a query, a schema
discovery, a health check, a connection test, an AI-generated query, a backup, a restore, a
migration, a confirmed write), goes through ``read`` or ``write`` here, and both write an
audit entry through ``audit.service.record`` in every outcome: success, refusal by the
database, error, timeout. The connector openers (``connectors/openers.py``) are imported
by this module only; ``tests/unit/connections/test_one_door.py`` fails the build if any
other module imports them or creates a database client itself.

``write`` additionally refuses to open anything unless the confirmation it is given reads
back from DBPilot's own database as ``executing``, the state the confirmation service
commits together with the ``*.confirmed`` audit entry. So the pre-execution audit record of
a write (actor, exact statement, connection) is durable before the statement is sent (AC5).
The gateway, not the work, commits a write, and only while the caller is still waiting:
a write that runs past its deadline is stopped and rolled back, never committed after its
failure was reported.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.engine import Connection as SaConnection
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import Actor, record
from dbpilot.connections.connectors.mongo import MongoReader
from dbpilot.connections.connectors.openers import open_mongo, open_redis, open_sql
from dbpilot.connections.connectors.redis_source import RedisReader
from dbpilot.connections.connectors.sql import WriteStoppedError, commit
from dbpilot.connections.connectors.targets import (
    DEFAULT_PORTS,
    MongoTarget,
    RedisTarget,
    SqlTarget,
    Target,
)
from dbpilot.connections.models import Connection
from dbpilot.core import metrics
from dbpilot.core.container import Container
from dbpilot.core.errors import AppError
from dbpilot.core.net import HostRefusedError, vet_host
from dbpilot.core.text import first_line

log = logging.getLogger("dbpilot.gateway")

T = TypeVar("T")
Handle = SaConnection | MongoReader | RedisReader
SQLITE_MAGIC = b"SQLite format 3\x00"
# How long past a write's deadline the caller still waits for it before stopping it.
WRITE_GRACE_S = 5.0


def secret_context(conn: Connection) -> str:
    """The encryption context the password is sealed under: bound to this record."""
    return f"connection:{conn.id}:password"


def error_text(exc: BaseException) -> str:
    """The database's own message, first line, without bound parameters."""
    orig = getattr(exc, "orig", None)
    text = str(orig) if isinstance(exc, sa.exc.DBAPIError) and orig is not None else str(exc)
    return first_line(text) or type(exc).__name__


async def _sqlite_copy(container: Container, key: str) -> Path:
    """A local copy of a stored SQLite file for reading. Keys are immutable (every write
    produces a new key), so a copy is downloaded once per process and reused."""
    cache = Path(container.settings.work_dir) / "sqlite"
    await asyncio.to_thread(cache.mkdir, parents=True, exist_ok=True)
    path = cache / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".sqlite")
    if not await asyncio.to_thread(path.exists):
        partial = cache / f"{path.name}.{uuid.uuid4().hex}.part"
        await container.storage.get_file(key, partial)
        await asyncio.to_thread(os.replace, partial, path)
    return path


@asynccontextmanager
async def _target(
    container: Container, conn: Connection, password: str | None, *, writable: bool = False
) -> AsyncIterator[Target]:
    """Everything needed to open the database: the sealed password opened, the SQLite file
    fetched (a private writable copy for writes), the host checked as it resolves."""
    cfg: dict[str, Any] = conn.config or {}
    if conn.kind == "sqlite":
        if writable:
            work = Path(container.settings.work_dir)
            await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=work, suffix=".sqlite")
            os.close(fd)
            path = Path(name)
            try:
                if conn.file_key:
                    await container.storage.get_file(conn.file_key, path)
                yield SqlTarget(kind="sqlite", sqlite_path=str(path))
            finally:
                await asyncio.to_thread(path.unlink, True)
            return
        if not conn.file_key:
            raise AppError("This SQLite connection has no database file", code="sqlite_missing")
        yield SqlTarget(
            kind="sqlite", sqlite_path=str(await _sqlite_copy(container, conn.file_key))
        )
        return
    host = str(cfg.get("host", ""))
    port = int(cfg.get("port") or DEFAULT_PORTS[conn.kind])
    try:
        # The drivers connect to this vetted address, not to a fresh lookup of the name.
        address = await vet_host(host, port, allow_private=container.settings.source_allow_private)
    except HostRefusedError as exc:
        raise AppError(exc.message, code=exc.code) from exc
    if password is None and conn.secret_sealed:
        password = await container.sealer.open(conn.secret_sealed, secret_context(conn))
    ssl_mode = str(cfg.get("ssl_mode") or ("prefer" if conn.kind == "postgresql" else "disable"))
    if conn.kind == "mongodb":
        yield MongoTarget(
            host=host,
            port=port,
            database=str(cfg.get("database", "")),
            username=str(cfg.get("username") or ""),
            password=password,
            auth_source=str(cfg.get("auth_source") or "admin"),
            ssl_mode=ssl_mode,
            address=address,
        )
    elif conn.kind == "redis":
        yield RedisTarget(
            host=host,
            port=port,
            db=int(cfg.get("database") or 0),
            username=str(cfg.get("username") or ""),
            password=password,
            ssl_mode=ssl_mode,
            address=address,
        )
    else:
        yield SqlTarget(
            kind=conn.kind,
            host=host,
            port=port,
            database=str(cfg.get("database", "")),
            username=str(cfg.get("username", "")),
            password=password,
            ssl_mode=ssl_mode,
            default_schema=cfg.get("default_schema"),
            address=address,
        )


def _run_read[T](target: Target, timeout_s: int, snapshot: bool, work: Callable[[Handle], T]) -> T:
    if isinstance(target, MongoTarget):
        with open_mongo(target, timeout_s) as reader:
            return work(reader)
    if isinstance(target, RedisTarget):
        with open_redis(target, timeout_s) as redis_reader:
            return work(redis_reader)
    with open_sql(target, timeout_s, mode="read", snapshot=snapshot) as c:
        return work(c)


@dataclass
class WriteControl:
    """Who decides a write's fate when its deadline passes while it runs. The worker thread
    commits only while the caller still waits; the caller stops waiting only before the
    commit has begun. Never both, so the outcome DBPilot reports is the one that happened."""

    deadline: float
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _abandoned: bool = False
    _committing: bool = False

    def stopped(self) -> bool:
        """For the work, between statements: whether to stop now (nothing more runs)."""
        return self._abandoned or time.monotonic() > self.deadline

    def begin_commit(self) -> bool:
        with self._lock:
            if self.stopped():
                self._abandoned = True
                return False
            self._committing = True
            return True

    def abandon(self) -> bool:
        """Stop waiting. False when the commit has already begun: wait for it instead."""
        with self._lock:
            if self._committing:
                return False
            self._abandoned = True
            return True


WriteWork = Callable[[SaConnection, Callable[[], bool]], T]


def _run_write[T](
    target: SqlTarget, timeout_s: int, work: WriteWork[T], control: WriteControl
) -> T:
    with open_sql(target, timeout_s, mode="write") as c:
        result = work(c, control.stopped)
        if not control.begin_commit():
            raise WriteStoppedError("the write ran past its time limit and was rolled back")
        commit(c)
        return result


def is_saved(conn: Connection) -> bool:
    """False for a connection being tested before it is saved (no row exists)."""
    return bool(sa.inspect(conn).has_identity)


async def read[T](
    container: Container,
    session: AsyncSession,
    conn: Connection,
    actor: Actor,
    *,
    action: str,
    work: Callable[[Handle], T],
    org_id: uuid.UUID | None,
    details: dict[str, Any] | None = None,
    summarize: Callable[[T], dict[str, Any]] | None = None,
    on_done: Callable[[T | None, BaseException | None], dict[str, Any] | None] | None = None,
    timeout_s: int | None = None,
    password: str | None = None,
    snapshot: bool = False,
) -> T:
    """Open ``conn`` read-only (database-enforced where the engine allows), run ``work`` on
    it in a worker thread, then record the audit entry and commit, whatever happened.

    ``on_done(result, error)`` may add the caller's own rows to the session (they commit
    with the audit entry) and return extra audit details."""
    limit = timeout_s or conn.timeout_s
    started = time.perf_counter()
    result: T | None = None
    error: BaseException | None = None
    try:
        async with _target(container, conn, password) as target:
            # The database enforces ``limit``; this only stops waiting on a hung network.
            result = await asyncio.wait_for(
                asyncio.to_thread(_run_read, target, limit, snapshot, work), timeout=limit + 30
            )
        return result
    except BaseException as exc:
        error = exc
        raise
    finally:
        await _finish(
            session,
            conn,
            actor,
            action,
            org_id,
            "read",
            started,
            details,
            summarize,
            on_done,
            result,
            error,
        )


async def write[T](
    container: Container,
    session: AsyncSession,
    conn: Connection,
    actor: Actor,
    *,
    confirmation_id: uuid.UUID,
    action: str,
    work: WriteWork[T],
    org_id: uuid.UUID | None,
    details: dict[str, Any] | None = None,
    summarize: Callable[[T], dict[str, Any]] | None = None,
    on_done: Callable[[T | None, BaseException | None], dict[str, Any] | None] | None = None,
    timeout_s: int | None = None,
    deadline_s: int | None = None,
) -> T:
    """Open ``conn`` write-capable for one confirmed action. Refuses unless the
    confirmation is committed as ``executing`` (so its ``*.confirmed`` audit entry already
    exists). SQLite writes go to a private copy that replaces the stored file only when the
    statements succeed and nobody else changed it meanwhile.

    ``timeout_s`` bounds each statement (the session's own timeout); ``deadline_s`` the
    whole write. ``work(connection, stopped)`` runs the statements and asks ``stopped()``
    between them; the gateway commits after it returns, if the deadline has not passed."""
    from dbpilot.confirmations.models import Confirmation

    status = await session.scalar(
        select(Confirmation.status)
        .where(Confirmation.id == confirmation_id, Confirmation.connection_id == conn.id)
        .execution_options(populate_existing=True)
    )
    if status != "executing":
        raise RuntimeError("a write needs a committed, executing confirmation")
    if conn.kind not in ("postgresql", "mysql", "mssql", "sqlite"):
        raise AppError("Writes are not offered for this engine", code="engine_not_supported")
    limit = timeout_s or conn.timeout_s
    budget = deadline_s or limit + 60
    started = time.perf_counter()
    result: T | None = None
    error: BaseException | None = None
    try:
        async with _target(container, conn, None, writable=True) as target:
            assert isinstance(target, SqlTarget)
            control = WriteControl(deadline=time.monotonic() + budget)
            task = asyncio.ensure_future(
                asyncio.to_thread(_run_write, target, limit, work, control)
            )
            # An abandoned write ends in WriteStoppedError on its own thread; read it there.
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(task), timeout=budget + WRITE_GRACE_S
                )
            except TimeoutError:
                if control.abandon():
                    raise WriteStoppedError(
                        f"the write did not finish within {budget} s; it was stopped before "
                        "committing and rolled back"
                    ) from None
                result = await task  # already committing: its outcome is the real one
            except asyncio.CancelledError:
                control.abandon()  # nothing commits after this unless it already began
                raise
            if conn.kind == "sqlite":
                await _replace_sqlite_file(container, session, conn, Path(str(target.sqlite_path)))
        return result
    except BaseException as exc:
        error = exc
        raise
    finally:
        await _finish(
            session,
            conn,
            actor,
            action,
            org_id,
            "write",
            started,
            {**(details or {}), "confirmation_id": str(confirmation_id)},
            summarize,
            on_done,
            result,
            error,
        )


async def _replace_sqlite_file(
    container: Container, session: AsyncSession, conn: Connection, path: Path
) -> None:
    """Store the written copy under a new key and point the connection at it, only if the
    connection still points at the file this write started from."""
    head = await asyncio.to_thread(lambda: path.read_bytes()[:16])
    if head != SQLITE_MAGIC:
        raise AppError("The written SQLite file is not a database", code="sqlite_invalid")
    key = f"projects/{conn.project_id}/connections/{conn.id}/{uuid.uuid4().hex}.sqlite"
    await container.storage.put_file(key, path, "application/vnd.sqlite3")
    size = await asyncio.to_thread(lambda: path.stat().st_size)
    base = conn.file_key
    clause = Connection.file_key.is_(None) if base is None else Connection.file_key == base
    changed = await session.execute(
        update(Connection)
        .where(Connection.id == conn.id, clause)
        .values(file_key=key, file_size=size)
    )
    if getattr(changed, "rowcount", 0) != 1:
        await container.storage.delete(key)
        raise AppError(
            "The SQLite file changed while this write ran; nothing was applied. Try again",
            code="sqlite_conflict",
        )
    conn.file_key, conn.file_size = key, size


async def _finish(
    session: AsyncSession,
    conn: Connection,
    actor: Actor,
    action: str,
    org_id: uuid.UUID | None,
    mode: str,
    started: float,
    details: dict[str, Any] | None,
    summarize: Callable[[Any], dict[str, Any]] | None,
    on_done: Callable[[Any, BaseException | None], dict[str, Any] | None] | None,
    result: Any,
    error: BaseException | None,
) -> None:
    elapsed = time.perf_counter() - started
    outcome = "ok" if error is None else "error"
    metrics.CONNECTOR_CALLS.labels(conn.kind, action, outcome).inc()
    metrics.CONNECTOR_SECONDS.labels(conn.kind, action).observe(elapsed)
    extra: dict[str, Any] = {}
    try:
        if error is None and summarize is not None:
            extra.update(summarize(result))
        if on_done is not None:
            # A result with an error (the SQLite copy was written but never stored) is no
            # success: the caller sees the error alone.
            extra.update(on_done(result if error is None else None, error) or {})
    except Exception:  # a broken summary must never cost the audit entry
        log.exception("audit summary failed", extra={"action": action})
    if error is not None:
        extra["error"] = (
            "timed out waiting for the database"
            if isinstance(error, TimeoutError)
            else error_text(error)
        )
    saved = is_saved(conn)
    payload: dict[str, Any] = {
        **(details or {}),
        **extra,
        "connection": conn.name,
        "engine": conn.kind,
        "mode": mode,
        "outcome": outcome,
        "duration_ms": round(elapsed * 1000, 1),
    }
    if not saved:
        payload["unsaved"] = True
        payload["host"] = (conn.config or {}).get("host")
    for reserved in (
        "session",
        "actor",
        "action",
        "target_type",
        "target_id",
        "org_id",
        "project_id",
        "connection_id",
    ):
        payload.pop(reserved, None)
    record(
        session,
        actor,
        action,
        target_type="connection",
        target_id=conn.id if saved else None,
        org_id=org_id,
        project_id=conn.project_id,
        connection_id=conn.id if saved else None,
        **payload,
    )
    try:
        await session.commit()
    except Exception:
        log.exception("audit commit failed", extra={"action": action})
        await session.rollback()
        if error is None:
            raise
