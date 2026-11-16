"""Schema comparisons: two snapshots, their diff, and the migration script a person may
choose to run (DBPilot never runs it itself)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, new_id


class SchemaComparison(Base):
    __tablename__ = "schema_comparisons"
    __table_args__ = (
        Index("ix_schema_comparisons_project_created", "project_id", "created_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    from_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schema_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("schema_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    to_connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # The script that turns the "from" database into the "to" schema.
    migration_sql: Mapped[str] = mapped_column(Text, nullable=False, default="")
    statements: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    destructive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Whether the confirmed-write path can describe every statement (and so run it).
    runnable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    runnable_detail: Mapped[str | None] = mapped_column(Text)
    statement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
