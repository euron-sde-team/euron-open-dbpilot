from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from dbpilot.core.schemas import ApiModel


class ComparisonIn(ApiModel):
    """Two snapshots, or two connections (their latest snapshots). The script turns the
    ``from`` database into the ``to`` schema, so it is meant to run on ``from``."""

    from_snapshot_id: uuid.UUID | None = None
    to_snapshot_id: uuid.UUID | None = None
    from_connection_id: uuid.UUID | None = None
    to_connection_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _pairs(self) -> ComparisonIn:
        snaps = self.from_snapshot_id is not None and self.to_snapshot_id is not None
        conns = self.from_connection_id is not None and self.to_connection_id is not None
        if snaps == conns:
            raise ValueError(
                "give from_snapshot_id and to_snapshot_id, or from_connection_id and "
                "to_connection_id"
            )
        return self


class ScriptStatement(ApiModel):
    sql: str
    destructive: bool


class ComparisonOut(ApiModel):
    id: uuid.UUID
    engine: str
    from_snapshot_id: uuid.UUID
    to_snapshot_id: uuid.UUID
    from_connection_id: uuid.UUID
    to_connection_id: uuid.UUID
    from_captured_at: datetime | None = None
    to_captured_at: datetime | None = None
    identical: bool
    diff: dict[str, Any]
    migration_sql: str = Field(description="Never run by DBPilot; run it through /run")
    statements: list[ScriptStatement]
    notes: list[str]
    destructive: bool
    runnable: bool
    runnable_detail: str | None
    created_by_label: str
    created_at: datetime


class RunComparisonIn(ApiModel):
    note: str = Field("", max_length=2000)
