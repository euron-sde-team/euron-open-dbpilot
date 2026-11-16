from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from dbpilot.core.schemas import ApiModel


class BackupCreate(ApiModel):
    connection_id: uuid.UUID


class BackupOut(ApiModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    connection_name: str
    engine: str
    trigger: Literal["manual", "scheduled"]
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None
    size_bytes: int | None
    checksum: str | None
    format: str
    table_count: int
    row_count: int
    tables: dict[str, int]
    integrity_ok: bool | None
    integrity_detail: str | None
    integrity_checked_at: datetime | None
    error: str | None
    schedule_id: uuid.UUID | None
    created_by_label: str


class RestoreIn(ApiModel):
    """Where to restore: an existing connection to an empty database, or (SQLite backups
    only) a new SQLite connection DBPilot creates."""

    target_connection_id: uuid.UUID | None = None
    new_sqlite_name: str | None = Field(None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def _one_target(self) -> RestoreIn:
        if (self.target_connection_id is None) == (self.new_sqlite_name is None):
            raise ValueError("give exactly one of target_connection_id and new_sqlite_name")
        return self


class ScheduleIn(ApiModel):
    connection_id: uuid.UUID
    interval_minutes: int = Field(1440, ge=5, le=60 * 24 * 31, description="How often")
    retention_count: int = Field(7, ge=1, le=365, description="Scheduled backups kept")
    enabled: bool = True


class ScheduleUpdate(ApiModel):
    interval_minutes: int | None = Field(None, ge=5, le=60 * 24 * 31)
    retention_count: int | None = Field(None, ge=1, le=365)
    enabled: bool | None = None


class ScheduleOut(ApiModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    connection_name: str
    interval_minutes: int
    retention_count: int
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    last_status: str | None
    created_by_label: str
    created_at: datetime
