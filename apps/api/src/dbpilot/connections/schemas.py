from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from dbpilot.core.net import check_host
from dbpilot.core.schemas import ApiModel

ConnectionKind = Literal["postgresql", "mysql", "sqlite", "mssql", "mongodb", "redis"]
ServerKind = Literal["postgresql", "mysql", "mssql", "mongodb", "redis"]
SslMode = Literal["disable", "prefer", "require", "verify-full"]


class Limits(ApiModel):
    max_rows: int = Field(1000, ge=1, le=100_000, description="Rows a result may return")
    max_scan_rows: int = Field(
        10_000_000,
        ge=1,
        le=10_000_000_000,
        description="Rows (documents, keys) the engine's planner may expect to read; a bigger "
        "plan is refused before it runs",
    )
    timeout_s: int = Field(30, ge=1, le=600, description="Seconds a statement may run")


class ConnectionCreate(Limits):
    """A database server. The password is write-only: it is sealed and never returned."""

    name: str = Field(min_length=1, max_length=120)
    kind: ServerKind
    host: str = Field(min_length=1, max_length=255)
    port: int | None = Field(None, ge=1, le=65535)
    database: str = Field(
        "",
        max_length=128,
        description="Database name; for Redis the database number (0 to 15)",
    )
    username: str = Field("", max_length=128)
    password: str | None = Field(None, max_length=1000)
    ssl_mode: SslMode | None = None
    default_schema: str | None = Field(None, max_length=128)
    auth_source: str | None = Field(None, max_length=128, description="MongoDB only")
    sample_values: bool = False
    description: str = Field("", max_length=2000)

    @field_validator("host")
    @classmethod
    def _check_host(cls, v: str) -> str:
        return check_host(v)

    @model_validator(mode="after")
    def _per_kind(self) -> ConnectionCreate:
        if self.kind == "redis":
            db = self.database or "0"
            if not db.isdigit() or int(db) > 15:
                raise ValueError("for Redis, database is the database number, 0 to 15")
            self.database = db
        elif not self.database:
            raise ValueError("database is required")
        if self.kind in ("postgresql", "mysql", "mssql") and not self.username:
            raise ValueError("username is required")
        return self


class ConnectionUpdate(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    host: str | None = Field(None, min_length=1, max_length=255)
    port: int | None = Field(None, ge=1, le=65535)
    database: str | None = Field(None, max_length=128)
    username: str | None = Field(None, max_length=128)
    # A new password replaces the sealed one; "" removes it; omit it to keep it.
    password: str | None = Field(None, max_length=1000)
    ssl_mode: SslMode | None = None
    default_schema: str | None = Field(None, max_length=128)
    auth_source: str | None = Field(None, max_length=128)
    max_rows: int | None = Field(None, ge=1, le=100_000)
    max_scan_rows: int | None = Field(None, ge=1, le=10_000_000_000)
    timeout_s: int | None = Field(None, ge=1, le=600)
    sample_values: bool | None = None
    description: str | None = Field(None, max_length=2000)

    @field_validator("host")
    @classmethod
    def _check(cls, v: str | None) -> str | None:
        return check_host(v) if v is not None else None


class MyAccess(ApiModel):
    can_write: bool = Field(description="Whether you may prepare confirmed writes on it")
    via: Literal["role", "grant", "all_members"]


class ConnectionOut(ApiModel):
    id: uuid.UUID
    name: str
    kind: ConnectionKind
    description: str
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    ssl_mode: str | None = None
    default_schema: str | None = None
    auth_source: str | None = None
    # Never the password: only whether one is stored.
    has_password: bool = False
    file_name: str | None = None
    file_size: int | None = None
    max_rows: int
    max_scan_rows: int
    timeout_s: int
    sample_values: bool
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_message: str | None
    server_version: str | None
    schema_objects: int | None = Field(None, description="Objects in the latest schema snapshot")
    schema_captured_at: datetime | None = None
    access: MyAccess
    created_at: datetime


class TestResult(ApiModel):
    ok: bool
    message: str
    server_version: str | None = None
    objects: int | None = Field(None, description="Tables, views, collections or keys visible")
    latency_ms: float
    read_only: bool | None = Field(
        None,
        description="True when the database confirmed the session is read-only; false on "
        "SQL Server (rollback-only sessions); null for MongoDB and Redis (read-only allowlist)",
    )
    read_only_detail: str = ""


class GrantIn(ApiModel):
    user_id: uuid.UUID | None = Field(
        None, description="A project member; null grants every project member"
    )
    can_write: bool = Field(False, description="May prepare confirmed writes on the connection")


class GrantOut(ApiModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    user_id: uuid.UUID | None
    email: str | None
    name: str | None
    all_members: bool
    can_write: bool
    created_at: datetime
