"""Restores (AC7): a backup replayed into an empty database, behind the same two-step
confirmation as a write, with its own audit entries (``restore.requested``,
``restore.confirmed`` before anything runs, ``restore.executed`` with the verified counts).

Only project admins restore. The target must be the backup's engine and hold none of the
tables the backup creates (checked against the live target first), or, for SQLite, a new
SQLite connection DBPilot creates. The stored dump must match its recorded checksum before a
single statement is replayed, and after the replay every table's rows are counted against
the backup.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.engine import Connection as SaConnection
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import record
from dbpilot.backups import service as backups
from dbpilot.backups.models import Backup
from dbpilot.backups.schemas import RestoreIn
from dbpilot.confirmations import service as confirmations
from dbpilot.confirmations.models import Confirmation
from dbpilot.connections import gateway
from dbpilot.connections.access import accessible, require_write
from dbpilot.connections.connectors.discovery import qualifies, schemas_of
from dbpilot.connections.connectors.dump import RestoreSummary, restore
from dbpilot.connections.models import Connection
from dbpilot.connections.service import friendly
from dbpilot.core import metrics
from dbpilot.core.container import Container
from dbpilot.core.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableError,
)
from dbpilot.core.security import sha256_text
from dbpilot.projects.access import Principal

# A restore's statements (an index build on a large table, a batch of rows) get at least
# this long each, and the whole restore at most RESTORE_DEADLINE_S; past it, it is stopped
# and rolled back rather than left to commit after its failure was reported.
RESTORE_STATEMENT_TIMEOUT_S = 600
RESTORE_DEADLINE_S = 3 * 3600


async def _existing_tables(
    container: Container, session: AsyncSession, p: Principal, target: Connection
) -> set[str]:
    def work(handle: gateway.Handle) -> set[str]:
        assert isinstance(handle, SaConnection)
        inspector = sa.inspect(handle)
        names: set[str] = set()
        for s in schemas_of(handle, None):
            for t in inspector.get_table_names(schema=s):
                names.add(f"{s}.{t}" if qualifies(handle.dialect.name) else t)
        return names

    try:
        return await gateway.read(
            container,
            session,
            target,
            p.actor,
            action="restore.target_checked",
            work=work,
            org_id=p.org_id,
        )
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            friendly(gateway.error_text(exc)), code="restore_target_unreachable"
        ) from exc


async def prepare(
    container: Container,
    session: AsyncSession,
    p: Principal,
    backup: Backup,
    source: Connection,
    body: RestoreIn,
) -> tuple[Confirmation, str, Connection]:
    if not p.manages:
        raise PermissionDeniedError(
            "Restores need the admin role in this project", code="insufficient_role"
        )
    if backup.status != "succeeded":
        raise UnprocessableError(
            "Only a completed backup can be restored", code="backup_incomplete"
        )
    if body.new_sqlite_name is not None:
        if backup.engine != "sqlite":
            raise UnprocessableError(
                "A new SQLite connection can only receive a SQLite backup", code="engine_mismatch"
            )
        name = body.new_sqlite_name.strip()
        taken = await session.scalar(
            select(Connection.id).where(
                Connection.project_id == p.project_id, Connection.name == name
            )
        )
        if taken:
            raise ConflictError(
                "A connection with this name exists in the project", code="name_taken"
            )
        holder, target_label, target_id = source, f"a new SQLite connection named {name}", None
    else:
        assert body.target_connection_id is not None
        access = await accessible(session, p, body.target_connection_id)
        require_write(access)
        holder = access.connection
        if holder.kind != backup.engine:
            raise UnprocessableError(
                f"A {backup.engine} backup restores into a {backup.engine} database, not "
                f"{holder.kind}",
                code="engine_mismatch",
            )
        clash = sorted(
            set(backup.tables or {}) & await _existing_tables(container, session, p, holder)
        )
        if clash:
            raise ConflictError(
                "The target already holds tables this backup would create. Restore into an "
                "empty database",
                code="restore_target_not_empty",
                details={"tables": clash},
            )
        name, target_label, target_id = None, holder.name, holder.id
    plan: dict[str, Any] = {
        "backup_id": str(backup.id),
        "source_connection_id": str(source.id),
        "source_connection": source.name,
        "target_connection_id": str(target_id) if target_id else None,
        "target": target_label,
        "new_sqlite_name": name,
        "engine": backup.engine,
        "backup_taken_at": backup.started_at.isoformat(),
        "tables": backup.tables,
        "total_rows": backup.row_count,
        "checksum": backup.checksum,
    }
    digest = sha256_text(
        json.dumps({"checksum": backup.checksum, "target": str(target_id or name)})
    )
    summary = (
        f"Restore {backup.table_count} tables ({backup.row_count:,} rows) from the "
        f"{backup.started_at:%Y-%m-%d %H:%M} UTC backup of {source.name} into {target_label}"
    )
    warnings = ["Every table in the backup is created in the target and filled with its rows"]
    if backup.engine == "mysql":
        warnings.append(
            "MySQL commits each table it creates: a failed restore can leave some tables behind"
        )
    c, token = await confirmations.create(
        session,
        p,
        holder,
        kind="restore",
        payload={"restore": plan, "warnings": warnings},
        digest=digest,
        summary=summary,
        ttl_s=container.settings.confirmation_ttl_s,
        audit_details={
            "backup_id": str(backup.id),
            "target": target_label,
            "tables": backup.table_count,
        },
        backup_id=backup.id,
    )
    return c, token, holder


async def execute(
    container: Container,
    session: AsyncSession,
    p: Principal,
    c: Confirmation,
    holder: Connection,
) -> RestoreSummary | None:
    """Run a confirmed restore (``c`` is committed as ``executing``)."""
    from dbpilot.schema import service as schemas

    plan = (c.payload or {}).get("restore") or {}
    backup = await session.get(Backup, c.backup_id) if c.backup_id else None
    if backup is None:
        await confirmations.finish(
            session, c, ok=False, result=None, error="The backup no longer exists"
        )
        raise NotFoundError("Backup not found")
    target = holder
    if plan.get("new_sqlite_name"):
        source = await session.get(Connection, uuid.UUID(plan["source_connection_id"]))
        target = Connection(
            id=uuid.uuid4(),
            project_id=c.project_id,
            name=str(plan["new_sqlite_name"]),
            kind="sqlite",
            config={},
            file_name=f"{plan['new_sqlite_name']}.sqlite",
            max_rows=source.max_rows if source else 1000,
            max_scan_rows=source.max_scan_rows if source else 10_000_000,
            timeout_s=source.timeout_s if source else 30,
            description=f"Restored from the backup of {plan.get('source_connection')}",
            created_by=p.user_id,
        )
        session.add(target)
        # The row must exist before the confirmation points at it (no ORM relationship
        # tells the flush which comes first).
        await session.flush()
        c.connection_id = target.id
        record(
            session,
            p.actor,
            "connection.created",
            target_type="connection",
            target_id=target.id,
            connection_id=target.id,
            name=target.name,
            engine="sqlite",
            restored_from=str(backup.id),
            **p.scope(),
        )
        await session.commit()
    summary: RestoreSummary | None = None
    error: str | None = None
    try:
        path = await backups.fetch(container, backup)
    except AppError as exc:
        await confirmations.finish(session, c, ok=False, result=None, error=exc.message)
        metrics.RESTORES.labels(backup.engine, "failed").inc()
        raise

    def work(handle: SaConnection, stopped: Callable[[], bool]) -> RestoreSummary:
        return restore(handle, path, stopped)

    try:
        summary = await gateway.write(
            container,
            session,
            target,
            p.actor,
            confirmation_id=c.id,
            action="restore.executed",
            work=work,
            org_id=p.org_id,
            timeout_s=max(target.timeout_s, RESTORE_STATEMENT_TIMEOUT_S),
            deadline_s=RESTORE_DEADLINE_S,
            details={"backup_id": str(backup.id), "checksum": backup.checksum},
            summarize=lambda s: {
                "tables": len(s.tables),
                "rows": sum(s.tables.values()),
                "mismatches": {k: list(v) for k, v in s.mismatches.items()},
            },
        )
    except AppError as exc:
        error = exc.message
    except Exception as exc:  # drivers raise many unrelated types
        error = friendly(gateway.error_text(exc))
    finally:
        await asyncio.to_thread(path.unlink, True)
    verified = summary is not None and not summary.mismatches
    if summary is not None:
        await schemas.invalidate(session, target)
        await session.commit()
    status = "succeeded" if verified else "failed"
    if summary is not None and not verified:
        error = "Row counts differ from the backup: " + json.dumps(
            {k: list(v) for k, v in summary.mismatches.items()}
        )
    await confirmations.finish(
        session,
        c,
        ok=verified,
        result={
            "target_connection_id": str(target.id),
            "tables": summary.tables if summary else {},
            "expected": summary.expected if summary else {},
            "verified": verified,
            "statements": summary.statements if summary else 0,
        },
        error=error,
    )
    metrics.RESTORES.labels(backup.engine, status).inc()
    return summary
