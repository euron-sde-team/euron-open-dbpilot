"""Connected databases and who may use them.

A connection's password is sealed (envelope encryption, bound to the record) and never
returned by the API; it is opened only by the gateway at the moment the connection is used
(AC1). Every connection carries the safety limits its queries run under (AC2).
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
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base, TimestampMixin, new_id

CONNECTION_KINDS = ("postgresql", "mysql", "sqlite", "mssql", "mongodb", "redis")


class Connection(TimestampMixin, Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_connections_project_name"),
        CheckConstraint(
            "kind IN ('postgresql', 'mysql', 'sqlite', 'mssql', 'mongodb', 'redis')",
            name="ck_connections_kind",
        ),
        CheckConstraint(
            "max_rows > 0 AND max_scan_rows > 0 AND timeout_s > 0", name="ck_connections_limits"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # host, port, database, username, ssl_mode, default_schema, auth_source: nothing secret.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # The password, sealed with the record id as context; never returned by the API.
    secret_sealed: Mapped[bytes | None] = mapped_column(LargeBinary)
    # SQLite: the database file in object storage (read from a private read-only copy).
    file_key: Mapped[str | None] = mapped_column(String(300))
    file_name: Mapped[str | None] = mapped_column(String(300))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    # Safety limits, enforced on every query against this database: rows a result may
    # return (the rest is cut and the result says so), rows the planner may expect to read
    # (a bigger plan is refused before it runs), and seconds a statement may run.
    max_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    max_scan_rows: Mapped[int] = mapped_column(BigInteger, nullable=False, default=10_000_000)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # Low cardinality text values go into AI prompts only when this is on (real data).
    sample_values: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_message: Mapped[str | None] = mapped_column(Text)
    server_version: Mapped[str | None] = mapped_column(String(200))
    # A confirmed DDL write marks the cached schema stale; the next read rediscovers it.
    schema_invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class ConnectionGrant(Base):
    """Who may use a connection besides the project's admins and owners: one person
    (``user_id``) or every project member (``user_id`` null), and whether they may prepare
    confirmed writes on it."""

    __tablename__ = "connection_grants"
    __table_args__ = (
        Index(
            "uq_connection_grants_user",
            "connection_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_connection_grants_everyone",
            "connection_id",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index("ix_connection_grants_project_user", "project_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    can_write: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
