"""Backups: a logical dump of a SQL connection, read through the audited gateway on a
read-only (snapshot) session, stored in object storage, checked by reading it back.

Manual backups run in the request; scheduled ones run in the worker (``dbpilot worker``,
``backups/schedules.py``). Both use ``run_backup``. Restores are in ``backups/restore.py``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Connection as SaConnection
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import Actor, record
from dbpilot.backups.models import Backup
from dbpilot.backups.schemas import BackupOut
from dbpilot.connections import gateway
from dbpilot.connections.access import visible_clause, visible_ids
from dbpilot.connections.connectors.dump import (
    BackupTooLargeError,
    DumpSummary,
    dump,
    sha256_file,
    verify,
)
from dbpilot.connections.models import Connection
from dbpilot.connections.service import friendly
from dbpilot.core import metrics
from dbpilot.core.container import Container
from dbpilot.core.errors import AppError, NotFoundError, UnprocessableError
from dbpilot.core.schemas import decode_cursor, encode_cursor
from dbpilot.projects.access import Principal

SQL_ENGINES = ("postgresql", "mysql", "mssql", "sqlite")
BACKUP_TIMEOUT_S = 300


def to_out(b: Backup, conn: Connection) -> BackupOut:
    return BackupOut(
        id=b.id,
        connection_id=b.connection_id,
        connection_name=conn.name,
        engine=b.engine,
        trigger=b.trigger,
        status=b.status,
        started_at=b.started_at,
        finished_at=b.finished_at,
        size_bytes=b.size_bytes,
        checksum=b.checksum,
        format=b.format,
        table_count=b.table_count,
        row_count=b.row_count,
        tables={str(k): int(v) for k, v in (b.tables or {}).items()},
        integrity_ok=b.integrity_ok,
        integrity_detail=b.integrity_detail,
        integrity_checked_at=b.integrity_checked_at,
        error=b.error,
        schedule_id=b.schedule_id,
        created_by_label=b.created_by_label,
    )


def sql_only(conn: Connection) -> None:
    if conn.kind not in SQL_ENGINES:
        raise UnprocessableError(
            f"Backups cover the SQL engines; {conn.kind} backups are on the roadmap",
            code="engine_not_supported",
        )


def _temp(container: Container, suffix: str) -> Path:
    work = Path(container.settings.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=work, suffix=suffix)
    os.close(fd)
    return Path(name)


async def run_backup(
    container: Container,
    session: AsyncSession,
    conn: Connection,
    actor: Actor,
    *,
    org_id: uuid.UUID | None,
    trigger: str,
    schedule_id: uuid.UUID | None = None,
) -> Backup:
    """Dump ``conn`` and store it. A failure is recorded on the backup, never raised."""
    sql_only(conn)
    backup = Backup(
        id=uuid.uuid4(),
        project_id=conn.project_id,
        connection_id=conn.id,
        schedule_id=schedule_id,
        engine=conn.kind,
        trigger=trigger,
        status="running",
        started_at=datetime.now(UTC),
        created_by=actor.id,
        created_by_label=actor.label,
    )
    session.add(backup)
    await session.commit()
    started = time.perf_counter()
    path = await asyncio.to_thread(_temp, container, ".dbpdump.gz")
    schema = (conn.config or {}).get("default_schema")
    max_rows = container.settings.backup_max_rows

    def work(handle: gateway.Handle) -> DumpSummary:
        assert isinstance(handle, SaConnection)
        return dump(handle, path, schema=schema, max_rows=max_rows)

    try:
        summary = await gateway.read(
            container,
            session,
            conn,
            actor,
            action="backup.created",
            work=work,
            org_id=org_id,
            snapshot=True,
            timeout_s=max(conn.timeout_s, BACKUP_TIMEOUT_S),
            details={"backup_id": str(backup.id), "trigger": trigger},
            summarize=lambda s: {"tables": len(s.tables), "rows": s.total_rows},
        )
        integrity = await asyncio.to_thread(verify, path)
        if not integrity.ok:
            raise AppError(
                f"The backup did not read back: {integrity.detail}", code="backup_corrupt"
            )
        backup.checksum = await asyncio.to_thread(sha256_file, path)
        backup.size_bytes = await asyncio.to_thread(lambda: path.stat().st_size)
        key = f"projects/{conn.project_id}/backups/{conn.id}/{backup.id}.dbpdump.gz"
        await container.storage.put_file(key, path, "application/gzip")
        backup.storage_key = key
        backup.status = "succeeded"
        backup.tables = summary.tables
        backup.table_count = len(summary.tables)
        backup.row_count = summary.total_rows
        backup.integrity_ok, backup.integrity_detail = True, integrity.detail
        backup.integrity_checked_at = datetime.now(UTC)
        if summary.skipped:
            backup.integrity_detail += ". Not included: " + "; ".join(summary.skipped)[:500]
    except BackupTooLargeError as exc:
        backup.status, backup.error = "failed", f"{exc}. Raise BACKUP_MAX_ROWS to back it up"
    except AppError as exc:
        backup.status, backup.error = "failed", exc.message
    except Exception as exc:  # drivers raise many unrelated types
        backup.status, backup.error = "failed", friendly(gateway.error_text(exc))
    finally:
        await asyncio.to_thread(path.unlink, True)
    backup.finished_at = datetime.now(UTC)
    elapsed = time.perf_counter() - started
    record(
        session,
        actor,
        "backup.completed" if backup.status == "succeeded" else "backup.failed",
        target_type="backup",
        target_id=backup.id,
        connection_id=conn.id,
        org_id=org_id,
        project_id=conn.project_id,
        trigger=trigger,
        tables=backup.table_count,
        rows=backup.row_count,
        size_bytes=backup.size_bytes,
        checksum=backup.checksum,
        error=backup.error,
    )
    await session.commit()
    metrics.BACKUPS.labels(conn.kind, trigger, backup.status).inc()
    metrics.BACKUP_SECONDS.labels(conn.kind).observe(elapsed)
    if backup.size_bytes:
        metrics.BACKUP_BYTES.inc(backup.size_bytes)
    return backup


async def fetch(container: Container, backup: Backup, *, check: bool = True) -> Path:
    """The stored dump in a local temp file; its checksum must match the recorded one."""
    if backup.status != "succeeded" or not backup.storage_key:
        raise UnprocessableError("This backup did not complete", code="backup_incomplete")
    path = await asyncio.to_thread(_temp, container, ".dbpdump.gz")
    try:
        await container.storage.get_file(backup.storage_key, path)
        if check and await asyncio.to_thread(sha256_file, path) != backup.checksum:
            raise AppError(
                "The stored backup does not match its checksum; it will not be used",
                code="backup_checksum_mismatch",
            )
    except BaseException:
        await asyncio.to_thread(path.unlink, True)
        raise
    return path


async def recheck(
    container: Container, session: AsyncSession, p: Principal, backup: Backup
) -> Backup:
    """Read the stored backup back again: checksum, every line, the row counts."""
    ok, detail = False, ""
    try:
        path = await fetch(container, backup)
    except AppError as exc:
        detail = exc.message
    else:
        try:
            integrity = await asyncio.to_thread(verify, path)
            ok, detail = integrity.ok, integrity.detail
        finally:
            await asyncio.to_thread(path.unlink, True)
    backup.integrity_ok, backup.integrity_detail = ok, detail
    backup.integrity_checked_at = datetime.now(UTC)
    record(
        session,
        p.actor,
        "backup.verified",
        target_type="backup",
        target_id=backup.id,
        connection_id=backup.connection_id,
        ok=ok,
        detail=detail,
        **p.scope(),
    )
    await session.commit()
    return backup


async def get(
    session: AsyncSession, p: Principal, backup_id: uuid.UUID
) -> tuple[Backup, Connection]:
    ids = await visible_ids(session, p)
    row = (
        await session.execute(
            select(Backup, Connection)
            .join(Connection, Connection.id == Backup.connection_id)
            .where(
                Backup.id == backup_id,
                Backup.project_id == p.project_id,
                visible_clause(Backup.connection_id, ids),
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Backup not found")
    return row[0], row[1]


async def list_backups(
    session: AsyncSession,
    p: Principal,
    *,
    connection_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[tuple[Backup, Connection]], str | None]:
    ids = await visible_ids(session, p)
    stmt = (
        select(Backup, Connection)
        .join(Connection, Connection.id == Backup.connection_id)
        .where(Backup.project_id == p.project_id, visible_clause(Backup.connection_id, ids))
    )
    if connection_id:
        stmt = stmt.where(Backup.connection_id == connection_id)
    after = decode_cursor(cursor)
    if after:
        at, _, last = after.partition("|")
        try:
            at_dt, last_id = datetime.fromisoformat(at), uuid.UUID(last)
            stmt = stmt.where(
                (Backup.started_at < at_dt) | ((Backup.started_at == at_dt) & (Backup.id < last_id))
            )
        except ValueError:
            pass
    rows = [
        (b, c)
        for b, c in (
            await session.execute(
                stmt.order_by(Backup.started_at.desc(), Backup.id.desc()).limit(limit + 1)
            )
        ).all()
    ]
    nxt = None
    if len(rows) > limit:
        last_b = rows[limit - 1][0]
        nxt = encode_cursor(f"{last_b.started_at.isoformat()}|{last_b.id}")
    return rows[:limit], nxt


async def delete(container: Container, session: AsyncSession, p: Principal, backup: Backup) -> None:
    key = backup.storage_key
    record(
        session,
        p.actor,
        "backup.deleted",
        target_type="backup",
        target_id=backup.id,
        connection_id=backup.connection_id,
        **p.scope(),
    )
    await session.delete(backup)
    await session.commit()
    if key:
        await container.storage.delete(key)


async def download(
    container: Container, session: AsyncSession, p: Principal, backup: Backup
) -> AsyncIterator[bytes]:
    """Stream the stored dump (project admins; audited: it holds the database's data)."""
    path = await fetch(container, backup)
    record(
        session,
        p.actor,
        "backup.downloaded",
        target_type="backup",
        target_id=backup.id,
        connection_id=backup.connection_id,
        size_bytes=backup.size_bytes,
        **p.scope(),
    )
    await session.commit()

    async def chunks() -> AsyncIterator[bytes]:
        try:
            handle = await asyncio.to_thread(path.open, "rb")
            try:
                while chunk := await asyncio.to_thread(handle.read, 1024 * 1024):
                    yield chunk
            finally:
                await asyncio.to_thread(handle.close)
        finally:
            await asyncio.to_thread(path.unlink, True)

    return chunks()


async def storage_keys_for(session: AsyncSession, connection_id: uuid.UUID) -> list[str]:
    return [
        k
        for k in (
            await session.scalars(
                select(Backup.storage_key).where(Backup.connection_id == connection_id)
            )
        ).all()
        if k
    ]
