"""Pending confirmations: the second, separate step every human-marked write, migration
run and restore passes before anything reaches the database (AC5, AC7, AC8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, new_id

CONFIRMATION_KINDS = ("write", "migration", "restore")
CONFIRMATION_STATUSES = ("pending", "executing", "executed", "failed", "cancelled", "expired")


class Confirmation(Base):
    __tablename__ = "confirmations"
    __table_args__ = (
        CheckConstraint("kind IN ('write', 'migration', 'restore')", name="ck_confirmations_kind"),
        CheckConstraint(
            "status IN ('pending', 'executing', 'executed', 'failed', 'cancelled', 'expired')",
            name="ck_confirmations_status",
        ),
        Index("ix_confirmations_project_created", "project_id", "created_at", "id"),
        Index("ix_confirmations_connection_created", "connection_id", "created_at"),
        # The worker's expiry sweep and the dashboards' pending counts.
        Index(
            "ix_confirmations_pending",
            "expires_at",
            postgresql_where="status = 'pending'",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    requested_by_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    # Only the hash of the one-time token is stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # SHA-256 of exactly what will run (the statements, or the restore plan).
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # The statements (write, migration) or the restore plan, exactly as shown.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    comparison_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_comparisons.id", ondelete="SET NULL")
    )
    backup_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backups.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
