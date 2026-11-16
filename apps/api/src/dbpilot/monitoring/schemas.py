from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from dbpilot.core.schemas import ApiModel


class SecurityCheck(ApiModel):
    id: str
    title: str
    status: Literal["pass", "warn", "fail", "info", "unknown"]
    detail: str
    setting: str | None = Field(None, description="The engine setting that was read")


class SecuritySummary(ApiModel):
    passed: int = 0
    warnings: int = 0
    failures: int = 0
    unknown: int = 0


class HealthCard(ApiModel):
    connection_id: uuid.UUID
    engine: str
    reachable: bool
    error: str | None = None
    server_version: str | None = None
    read_only_session: bool | None = Field(
        None, description="Whether the engine itself confirmed DBPilot's session is read-only"
    )
    latency_ms: float
    active_connections: int | None = None
    max_connections: int | None = None
    size_bytes: int | None = None
    uptime_s: int | None = None
    cache_hit_ratio: float | None = None
    objects: int | None = Field(None, description="Objects in the latest schema snapshot")
    rows: int | None = Field(None, description="Rows (documents, keys) in the latest snapshot")
    slow_queries: list[dict[str, Any]] = Field(default_factory=list)
    slow_queries_source: str | None = None
    slow_queries_note: str = ""
    index_advisories: list[dict[str, Any]] = Field(default_factory=list)
    security: list[SecurityCheck] = Field(default_factory=list)
    security_summary: SecuritySummary
    notes: list[str] = Field(default_factory=list)
    checked_at: datetime
    cached: bool = Field(False, description="Served from the short health cache")
    cache_ttl_s: int
