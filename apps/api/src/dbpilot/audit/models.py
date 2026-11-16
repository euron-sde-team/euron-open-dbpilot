"""The append-only audit log. A database trigger refuses UPDATE and DELETE (see the initial
migration), so history cannot be rewritten through DBPilot or by anyone with a SQL prompt
on its database short of dropping the trigger."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        # Keyset pagination per tenant, newest first, and the filters the API exposes.
        Index("ix_audit_log_project_id_id", "project_id", "id"),
        Index("ix_audit_log_org_id_id", "org_id", "id"),
        Index("ix_audit_log_connection_id_id", "connection_id", "id"),
        Index("ix_audit_log_project_actor_id", "project_id", "actor_id", "id"),
        Index(
            "ix_audit_log_project_action",
            "project_id",
            "action",
            postgresql_ops={"action": "varchar_pattern_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_email: Mapped[str | None] = mapped_column(String(320))
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
    # The connected database the action touched, when there is one.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    # The tenant the entry belongs to, so each organization and project reads only its own
    # trail (null for instance-level entries such as sign-ins).
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
