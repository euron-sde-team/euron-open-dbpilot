"""Every statement DBPilot ran or refused against a connected database, and saved queries.

Result rows are never stored here: they live in the key-value store for ``RESULT_TTL_S``
(paging and export), then are gone. The row keeps only the shape of the result.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, TimestampMixin, new_id

QUERY_STATUSES = ("succeeded", "refused", "failed", "timeout", "scan_limit")
QUERY_SOURCES = ("editor", "ai", "preview", "saved", "write", "migration")


class QueryRun(Base):
    __tablename__ = "query_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('succeeded', 'refused', 'failed', 'timeout', 'scan_limit')",
            name="ck_query_runs_status",
        ),
        CheckConstraint(
            "source IN ('editor', 'ai', 'preview', 'saved', 'write', 'migration')",
            name="ck_query_runs_source",
        ),
        CheckConstraint("mode IN ('read', 'write')", name="ck_query_runs_mode"),
        Index("ix_query_runs_project_created", "project_id", "created_at", "id"),
        Index("ix_query_runs_connection_created", "connection_id", "created_at", "id"),
        Index("ix_query_runs_project_actor", "project_id", "actor_id", "created_at"),
        Index("ix_query_runs_project_status", "project_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False, default="read")
    # The SQL as typed (or the MongoDB / Redis request, as JSON text).
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_affected: Mapped[int | None] = mapped_column(BigInteger)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # The database planner's own estimate of rows read, when it gave one.
    estimated_scan_rows: Mapped[int | None] = mapped_column(BigInteger)
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    ai_generation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_generations.id", ondelete="SET NULL"), index=True
    )
    confirmation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("confirmations.id", ondelete="SET NULL")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SavedQuery(TimestampMixin, Base):
    __tablename__ = "saved_queries"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_saved_queries_project_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
