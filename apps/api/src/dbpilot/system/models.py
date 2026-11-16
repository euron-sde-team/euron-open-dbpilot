"""Worker heartbeats: each ``dbpilot worker`` process records that it is alive on every
loop, so the API (and an operator) can see the scheduler is really running."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from dbpilot.core.db import Base


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Totals since the process started: loops, scheduled backups run, failures.
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
