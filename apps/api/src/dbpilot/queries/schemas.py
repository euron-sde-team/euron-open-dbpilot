from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from dbpilot.core.schemas import ApiModel

SqlDialect = Literal["postgresql", "mysql", "sqlite", "mssql"]
QueryStatus = Literal["succeeded", "refused", "failed", "timeout", "scan_limit"]
QuerySource = Literal["editor", "ai", "preview", "saved", "write", "migration"]


class SqlQueryIn(ApiModel):
    connection_id: uuid.UUID
    sql: str = Field(min_length=1, max_length=100_000)
    page_size: int = Field(100, ge=1, le=1000, description="Rows returned with the response")


class MongoQueryIn(ApiModel):
    """A read on a MongoDB collection. Filters and pipelines accept Extended JSON
    ({"$oid": ...}, {"$date": ...})."""

    connection_id: uuid.UUID
    collection: str = Field(min_length=1, max_length=120)
    operation: Literal["find", "aggregate", "count", "distinct"] = "find"
    filter: dict[str, Any] = Field(default_factory=dict)
    projection: dict[str, Any] | None = None
    sort: list[tuple[str, Literal[1, -1]]] = Field(default_factory=list)
    skip: int = Field(0, ge=0, le=1_000_000)
    limit: int | None = Field(None, ge=1, le=100_000)
    pipeline: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    field: str | None = Field(None, max_length=200, description="distinct only")
    page_size: int = Field(100, ge=1, le=1000)


class RedisQueryIn(ApiModel):
    connection_id: uuid.UUID
    command: str = Field(min_length=1, max_length=40)
    args: list[str] = Field(default_factory=list, max_length=64)
    page_size: int = Field(100, ge=1, le=1000)


class ResultColumn(ApiModel):
    name: str
    kind: str


class QueryRunOut(ApiModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    engine: str
    source: QuerySource
    mode: Literal["read", "write"]
    statement: str
    status: QueryStatus
    error: str | None = None
    error_code: str | None = None
    row_count: int
    rows_affected: int | None = None
    truncated: bool = Field(description="True when the result had more rows than max_rows")
    duration_ms: float
    estimated_scan_rows: int | None = None
    columns: list[ResultColumn]
    ai_generation_id: uuid.UUID | None = None
    confirmation_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None
    actor_label: str
    created_at: datetime


class RowsPage(ApiModel):
    query_id: uuid.UUID
    columns: list[ResultColumn]
    rows: list[list[Any]]
    offset: int
    limit: int
    total: int


class QueryResult(QueryRunOut):
    page: RowsPage | None = Field(None, description="The first rows, when the query succeeded")


class SqlTarget(ApiModel):
    """A statement and where it would run: a connection (its dialect) or a bare dialect."""

    sql: str = Field(min_length=1, max_length=100_000)
    connection_id: uuid.UUID | None = None
    dialect: SqlDialect | None = None

    @model_validator(mode="after")
    def _target(self) -> SqlTarget:
        if self.connection_id is None and self.dialect is None:
            raise ValueError("give a connection_id or a dialect")
        return self


class Validation(ApiModel):
    valid: bool = Field(description="True when it is exactly one read-only query")
    dialect: str
    reason: str | None = None
    code: str | None = None
    sql: str | None = None
    tables: list[str] = Field(default_factory=list)


class Formatted(ApiModel):
    sql: str
    dialect: str


class WriteIn(ApiModel):
    """A statement (or script) a person deliberately marks as a write. It is described and
    held for a second, separate confirmation; nothing runs yet."""

    connection_id: uuid.UUID
    sql: str = Field(min_length=1, max_length=100_000)
    note: str = Field("", max_length=2000)


class PreviewIn(ApiModel):
    object: str = Field(
        min_length=1, max_length=300, description="A table, view, collection or key group"
    )
    limit: int = Field(50, ge=1, le=1000)


class SavedQueryIn(ApiModel):
    connection_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=100_000)
    description: str = Field("", max_length=2000)


class SavedQueryUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    statement: str | None = Field(None, min_length=1, max_length=100_000)
    description: str | None = Field(None, max_length=2000)


class SavedQueryOut(ApiModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    name: str
    statement: str
    description: str
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
