from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from dbpilot.core.schemas import ApiModel

ConfirmationKind = Literal["write", "migration", "restore"]
ConfirmationStatus = Literal["pending", "executing", "executed", "failed", "cancelled", "expired"]


class StatementOut(ApiModel):
    index: int
    sql: str = Field(description="Exactly the text that will run")
    kind: str
    tables: list[str]
    summary: str
    destructive: bool
    has_where: bool | None = None


class ConfirmationOut(ApiModel):
    id: uuid.UUID
    kind: ConfirmationKind
    status: ConfirmationStatus
    connection_id: uuid.UUID
    connection_name: str
    engine: str
    summary: str
    statements: list[StatementOut] = Field(default_factory=list)
    restore: dict[str, Any] | None = Field(None, description="The restore plan (restores only)")
    warnings: list[str] = Field(default_factory=list)
    digest: str = Field(description="SHA-256 of exactly what will run; send it back to confirm")
    comparison_id: uuid.UUID | None = None
    backup_id: uuid.UUID | None = None
    requested_by: uuid.UUID | None
    requested_by_label: str
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class ConfirmationCreated(ConfirmationOut):
    confirm_token: str = Field(
        description="One-time token for the confirm call. Returned only here, never again"
    )


class ConfirmIn(ApiModel):
    confirm_token: str = Field(min_length=8, max_length=200)
    digest: str = Field(
        min_length=64, max_length=64, description="The digest shown with the statements"
    )


class ConfirmResult(ApiModel):
    confirmation: ConfirmationOut
    query_id: uuid.UUID | None = Field(None, description="The recorded run (writes, migrations)")
