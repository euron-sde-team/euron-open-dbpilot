"""Every AI assistant output, kept whether it was accepted or refused (the audit trail of
what the model proposed, against which schema)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, new_id

AI_KINDS = ("sql", "explain", "index_suggestions", "diagnose")
AI_STATUSES = ("accepted", "refused", "no_sql", "failed", "answered")


class AiGeneration(Base):
    __tablename__ = "ai_generations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('sql', 'explain', 'index_suggestions', 'diagnose')",
            name="ck_ai_generations_kind",
        ),
        CheckConstraint(
            "status IN ('accepted', 'refused', 'no_sql', 'failed', 'answered')",
            name="ck_ai_generations_status",
        ),
        Index("ix_ai_generations_project_created", "project_id", "created_at", "id"),
        Index("ix_ai_generations_connection_created", "connection_id", "created_at"),
        # The write path refuses any statement the assistant generated (AC4): it looks up
        # the fingerprints of what a person submits in this array (``&&``, GIN).
        Index(
            "ix_ai_generations_statement_fingerprints",
            "statement_fingerprints",
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # The question, or the statement to explain or diagnose.
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # The schema block the model was given (capped), and the snapshot it came from.
    schema_context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schema_snapshots.id", ondelete="SET NULL")
    )
    generated_sql: Mapped[str | None] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    guard_code: Mapped[str | None] = mapped_column(String(64))
    guard_reason: Mapped[str | None] = mapped_column(Text)
    unknown_identifiers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    tables: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # The whole output's fingerprint, and (below) that one plus each statement's own, so a
    # statement lifted out of a refused multi-statement answer is still recognized.
    sql_fingerprint: Mapped[str | None] = mapped_column(String(64))
    statement_fingerprints: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), nullable=False, default=list, server_default="{}"
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
