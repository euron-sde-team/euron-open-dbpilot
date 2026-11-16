"""The audit trail: one function writes every entry.

``record`` is the single shared code path for audit entries. It adds the entry to the
caller's session, so it commits in the same transaction as the change it describes. Every
operation that reaches a connected database goes through ``connections/gateway.py``, which
calls ``record`` in every outcome (success, refusal, error, timeout); writes, migrations and
restores also commit a ``*.confirmed`` entry here *before* the statement is sent.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dbpilot.audit.models import AuditLog
from dbpilot.core import metrics

MAX_DETAIL_TEXT = 100_000


@dataclass(frozen=True, slots=True)
class Actor:
    id: uuid.UUID | None
    email: str | None
    role: str
    ip: str | None = None

    @property
    def label(self) -> str:
        return self.email or self.role


# The scheduled-backup worker acts as this actor; the schedule's creator is in the details.
SCHEDULER = Actor(None, "system:scheduler", "system")


def _clean(value: Any) -> Any:
    """JSON-safe details: UUIDs, dates and decimals as text, long strings capped."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:MAX_DETAIL_TEXT]
    if isinstance(value, uuid.UUID | decimal.Decimal | dt.date | dt.datetime | dt.time):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_clean(v) for v in value]
    return str(value)[:MAX_DETAIL_TEXT]


def record(
    session: AsyncSession,
    actor: Actor,
    action: str,
    *,
    target_type: str | None = None,
    target_id: object | None = None,
    org_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    connection_id: uuid.UUID | None = None,
    **details: Any,
) -> AuditLog:
    """Add one audit entry to ``session`` (committed with the caller's transaction)."""
    entry = AuditLog(
        actor_id=actor.id,
        actor_email=actor.email,
        actor_role=actor.role,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        connection_id=connection_id,
        details=_clean(details),
        ip=actor.ip,
        org_id=org_id,
        project_id=project_id,
    )
    session.add(entry)
    metrics.AUDIT_ENTRIES.labels(action.split(".", 1)[0]).inc()
    return entry


async def list_entries(
    session: AsyncSession,
    *,
    limit: int,
    after_id: int | None = None,
    action: str | None = None,
    org_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    connection_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> list[AuditLog]:
    """Newest first, keyset-paginated on id; ``limit + 1`` rows so the caller can tell
    whether another page exists. ``action`` filters by prefix (``query.`` matches all)."""
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit + 1)
    if org_id is not None:
        stmt = stmt.where(AuditLog.org_id == org_id)
    if project_id is not None:
        stmt = stmt.where(AuditLog.project_id == project_id)
    if connection_id is not None:
        stmt = stmt.where(AuditLog.connection_id == connection_id)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if after_id is not None:
        stmt = stmt.where(AuditLog.id < after_id)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action, autoescape=True))
    return list((await session.scalars(stmt)).all())
