"""Backups of connected SQL databases and their schedules."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, TimestampMixin, new_id


class Backup(Base):
    __tablename__ = "backups"
    __table_args__ = (
        CheckConstraint("trigger IN ('manual', 'scheduled')", name="ck_backups_trigger"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_backups_status"),
        Index("ix_backups_project_started", "project_id", "started_at", "id"),
        Index("ix_backups_connection_started", "connection_id", "started_at", "id"),
        # The worker's reaper looks for backups whose process stopped.
        Index("ix_backups_running", "started_at", postgresql_where="status = 'running'"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backup_schedules.id", ondelete="SET NULL")
    )
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Where the dump lives (object storage) and what it holds.
    storage_key: Mapped[str | None] = mapped_column(String(300))
    format: Mapped[str] = mapped_column(String(32), nullable=False, default="dbpilot-dump/1")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum: Mapped[str | None] = mapped_column(String(64))
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Rows per table at backup time: what a restore is verified against.
    tables: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    integrity_ok: Mapped[bool | None] = mapped_column(Boolean)
    integrity_detail: Mapped[str | None] = mapped_column(Text)
    integrity_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")


class BackupSchedule(TimestampMixin, Base):
    """One schedule per connection; the worker runs it (``dbpilot worker``)."""

    __tablename__ = "backup_schedules"
    __table_args__ = (
        CheckConstraint("interval_minutes >= 5", name="ck_backup_schedules_interval"),
        CheckConstraint("retention_count >= 1", name="ck_backup_schedules_retention"),
        # The worker claims due schedules in next_run_at order.
        Index("ix_backup_schedules_due", "next_run_at", postgresql_where="enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connections.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")
