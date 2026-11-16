"""The two-step confirmation shared by writes, migration runs and restores.

1. ``create``: the request is described exactly (the statements as they will run, or the
   restore plan) and stored with a one-time token (only its hash is kept), the digest of
   what will run and an expiry. A ``<kind>.requested`` audit entry records who asked for
   what. Nothing touches the connected database.
2. ``begin``: the same person sends the token and the digest back. The token must match,
   the digest must still match what is stored, the confirmation must be pending and
   unexpired, and their write permission is checked again. The status becomes
   ``executing`` and a ``<kind>.confirmed`` audit entry (actor, exact statements,
   connection) is committed in the same transaction, before anything runs. The gateway's
   write door refuses to open for a confirmation that is not ``executing``.
3. ``finish``: the outcome is stored (the gateway has already written the
   ``<kind>.executed`` audit entry with the result).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.service import SCHEDULER, Actor, record
from dbpilot.confirmations.models import Confirmation
from dbpilot.confirmations.schemas import ConfirmationOut, StatementOut
from dbpilot.connections.access import visible_clause, visible_ids
from dbpilot.connections.models import Connection
from dbpilot.core import metrics
from dbpilot.core.errors import ConflictError, GoneError, NotFoundError, PermissionDeniedError
from dbpilot.core.schemas import decode_cursor, encode_cursor
from dbpilot.core.security import constant_time_equals, new_confirmation_token, token_hash
from dbpilot.projects.access import Principal


def to_out(c: Confirmation, conn: Connection) -> ConfirmationOut:
    payload = c.payload or {}
    return ConfirmationOut(
        id=c.id,
        kind=c.kind,
        status=c.status,
        connection_id=c.connection_id,
        connection_name=conn.name,
        engine=conn.kind,
        summary=c.summary,
        statements=[StatementOut.model_validate(s) for s in payload.get("statements", [])],
        restore=payload.get("restore"),
        warnings=list(payload.get("warnings", [])),
        digest=c.digest,
        comparison_id=c.comparison_id,
        backup_id=c.backup_id,
        requested_by=c.requested_by,
        requested_by_label=c.requested_by_label,
        created_at=c.created_at,
        expires_at=c.expires_at,
        decided_at=c.decided_at,
        executed_at=c.executed_at,
        result=c.result,
        error=c.error,
    )


async def create(
    session: AsyncSession,
    p: Principal,
    conn: Connection,
    *,
    kind: str,
    payload: dict[str, Any],
    digest: str,
    summary: str,
    ttl_s: int,
    audit_details: dict[str, Any],
    comparison_id: uuid.UUID | None = None,
    backup_id: uuid.UUID | None = None,
) -> tuple[Confirmation, str]:
    token, hashed = new_confirmation_token()
    now = datetime.now(UTC)
    c = Confirmation(
        id=uuid.uuid4(),
        project_id=p.project_id,
        connection_id=conn.id,
        kind=kind,
        status="pending",
        requested_by=p.user_id,
        requested_by_label=p.actor.label,
        token_hash=hashed,
        digest=digest,
        summary=summary,
        payload=payload,
        comparison_id=comparison_id,
        backup_id=backup_id,
        expires_at=now + timedelta(seconds=ttl_s),
        created_at=now,
    )
    session.add(c)
    record(
        session,
        p.actor,
        f"{kind}.requested",
        target_type="confirmation",
        target_id=c.id,
        connection_id=conn.id,
        connection=conn.name,
        engine=conn.kind,
        digest=digest,
        **audit_details,
        **p.scope(),
    )
    await session.commit()
    metrics.CONFIRMATIONS.labels(kind, "requested").inc()
    return c, token


async def get(
    session: AsyncSession, p: Principal, confirmation_id: uuid.UUID
) -> tuple[Confirmation, Connection]:
    """A confirmation on a connection the caller can see, or 404."""
    ids = await visible_ids(session, p)
    row = (
        await session.execute(
            select(Confirmation, Connection)
            .join(Connection, Connection.id == Confirmation.connection_id)
            .where(
                Confirmation.id == confirmation_id,
                Confirmation.project_id == p.project_id,
                visible_clause(Confirmation.connection_id, ids),
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Confirmation not found")
    return row[0], row[1]


async def begin(
    session: AsyncSession,
    p: Principal,
    confirmation_id: uuid.UUID,
    token: str,
    digest: str,
    *,
    can_write: bool,
) -> tuple[Confirmation, Connection]:
    """Validate the second step and commit ``executing`` with the ``*.confirmed`` audit
    entry, before anything runs. Row-locked, so a double submit runs once."""
    c, conn = await get(session, p, confirmation_id)
    # Lock the row and reload it: the identity map would otherwise hand back the values read
    # before the lock, and a concurrent double submit could both see "pending".
    locked = await session.scalar(
        select(Confirmation)
        .where(Confirmation.id == c.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert locked is not None
    c = locked
    now = datetime.now(UTC)

    def reject(reason: str) -> None:
        record(
            session,
            p.actor,
            f"{c.kind}.confirmation_rejected",
            target_type="confirmation",
            target_id=c.id,
            connection_id=conn.id,
            reason=reason,
            **p.scope(),
        )
        metrics.CONFIRMATIONS.labels(c.kind, "rejected").inc()

    if c.status != "pending":
        raise ConflictError(f"This confirmation is already {c.status}", code="confirmation_used")
    if c.expires_at < now:
        c.status = "expired"
        reject("expired")
        await session.commit()
        metrics.CONFIRMATIONS.labels(c.kind, "expired").inc()
        raise GoneError("This confirmation expired. Prepare it again", code="confirmation_expired")
    if c.requested_by != p.user_id:
        reject("different_person")
        await session.commit()
        raise PermissionDeniedError(
            "Only the person who prepared it can confirm it", code="not_your_confirmation"
        )
    if not constant_time_equals(c.token_hash, token_hash(token)):
        reject("wrong_token")
        await session.commit()
        raise PermissionDeniedError("The confirmation token does not match", code="token_mismatch")
    if not constant_time_equals(c.digest, digest):
        reject("digest_mismatch")
        await session.commit()
        raise ConflictError(
            "What you confirmed is not what was prepared. Review it again",
            code="statement_changed",
        )
    if not can_write:
        reject("write_not_granted")
        await session.commit()
        raise PermissionDeniedError(
            "You may no longer write to this connection", code="write_not_granted"
        )
    c.status = "executing"
    c.decided_at = now
    record(
        session,
        p.actor,
        f"{c.kind}.confirmed",
        target_type="confirmation",
        target_id=c.id,
        connection_id=conn.id,
        connection=conn.name,
        engine=conn.kind,
        digest=c.digest,
        statements=[s.get("sql") for s in (c.payload or {}).get("statements", [])],
        restore=(c.payload or {}).get("restore"),
        **p.scope(),
    )
    await session.commit()
    metrics.CONFIRMATIONS.labels(c.kind, "confirmed").inc()
    return c, conn


async def finish(
    session: AsyncSession,
    c: Confirmation,
    *,
    ok: bool,
    result: dict[str, Any] | None,
    error: str | None,
) -> None:
    await session.execute(
        update(Confirmation)
        .where(Confirmation.id == c.id)
        .values(
            status="executed" if ok else "failed",
            executed_at=datetime.now(UTC),
            result=result,
            error=error,
        )
    )
    await session.commit()
    await session.refresh(c)
    metrics.CONFIRMATIONS.labels(c.kind, "executed" if ok else "failed").inc()


async def cancel(session: AsyncSession, p: Principal, confirmation_id: uuid.UUID) -> Confirmation:
    c, conn = await get(session, p, confirmation_id)
    if c.status != "pending":
        raise ConflictError(f"This confirmation is already {c.status}", code="confirmation_used")
    if c.requested_by != p.user_id and not p.manages:
        raise PermissionDeniedError(
            "Only the person who prepared it, or a project admin, can cancel it",
            code="not_your_confirmation",
        )
    c.status = "cancelled"
    c.decided_at = datetime.now(UTC)
    record(
        session,
        p.actor,
        f"{c.kind}.cancelled",
        target_type="confirmation",
        target_id=c.id,
        connection_id=conn.id,
        **p.scope(),
    )
    await session.commit()
    metrics.CONFIRMATIONS.labels(c.kind, "cancelled").inc()
    return c


async def list_for(
    session: AsyncSession,
    p: Principal,
    *,
    status: str | None,
    kind: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[tuple[Confirmation, Connection]], str | None]:
    """Newest first, keyset-paginated, on connections the caller can see."""
    ids = await visible_ids(session, p)
    stmt = (
        select(Confirmation, Connection)
        .join(Connection, Connection.id == Confirmation.connection_id)
        .where(
            Confirmation.project_id == p.project_id,
            visible_clause(Confirmation.connection_id, ids),
        )
    )
    if status:
        stmt = stmt.where(Confirmation.status == status)
    if kind:
        stmt = stmt.where(Confirmation.kind == kind)
    after = decode_cursor(cursor)
    if after:
        at, _, last = after.partition("|")
        try:
            at_dt, last_id = datetime.fromisoformat(at), uuid.UUID(last)
            stmt = stmt.where(
                (Confirmation.created_at < at_dt)
                | ((Confirmation.created_at == at_dt) & (Confirmation.id < last_id))
            )
        except ValueError:
            pass
    rows = [
        (c, conn)
        for c, conn in (
            await session.execute(
                stmt.order_by(Confirmation.created_at.desc(), Confirmation.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    ]
    nxt = None
    if len(rows) > limit:
        last_c = rows[limit - 1][0]
        nxt = encode_cursor(f"{last_c.created_at.isoformat()}|{last_c.id}")
    return rows[:limit], nxt


async def expire_stale(session: AsyncSession, actor: Actor = SCHEDULER) -> int:
    """Mark pending confirmations past their expiry as expired (the worker's sweep)."""
    now = datetime.now(UTC)
    rows = (
        await session.scalars(
            select(Confirmation)
            .where(Confirmation.status == "pending", Confirmation.expires_at < now)
            .limit(500)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for c in rows:
        c.status = "expired"
        record(
            session,
            actor,
            f"{c.kind}.expired",
            target_type="confirmation",
            target_id=c.id,
            connection_id=c.connection_id,
            project_id=c.project_id,
        )
        metrics.CONFIRMATIONS.labels(c.kind, "expired").inc()
    await session.commit()
    return len(rows)
