from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from dbpilot.core.schemas import ApiModel


class Totals(ApiModel):
    projects: int | None = Field(None, description="Organization dashboards only")
    connections: int
    connections_by_engine: dict[str, int]
    queries_total: int
    queries_24h: int
    queries_refused_7d: int
    queries_failed_7d: int
    writes_executed_7d: int
    ai_accepted_7d: int
    ai_refused_7d: int
    backups_succeeded_7d: int
    backups_failed_7d: int
    last_backup_at: datetime | None
    pending_writes: int
    pending_migrations: int
    pending_restores: int
    schedules_enabled: int


class DashboardOut(ApiModel):
    scope: Literal["org", "project"]
    org_id: uuid.UUID
    project_id: uuid.UUID | None
    totals: Totals
    computed_at: datetime
    cached: bool = Field(description="Served from the 15 s response cache")
    cache_ttl_s: int
