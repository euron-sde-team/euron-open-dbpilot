"""Schema snapshots: point-in-time captures of a connection's discovered schema. The latest
one feeds the explorer, autocomplete, the AI assistant's context and the ER view; any two
can be compared."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, new_id


class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshots"
    __table_args__ = (
        # The latest snapshot of a connection, and keyset pages of its history.
        Index("ix_schema_snapshots_connection_captured", "connection_id", "captured_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    engine: Mapped[str] = mapped_column(String(16), nullable=False)
    # Tables, views, collections or key groups (``schema.schemas.SchemaObject``). Reads go
    # through the cache and only touch this column on a miss.
    objects: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    object_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # "refresh" (someone asked), "auto" (a read found it missing or stale), "write" (after
    # a confirmed DDL write), "restore".
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    captured_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
