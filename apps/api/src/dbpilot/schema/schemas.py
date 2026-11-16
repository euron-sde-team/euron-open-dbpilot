from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from dbpilot.core.schemas import ApiModel

ObjectKind = Literal["table", "view", "collection", "key_group"]


class SchemaColumn(ApiModel):
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False
    default: str | None = None
    autoincrement: bool = Field(False, description="An auto-generated key (serial, identity)")
    # Only when the connection allows sampling: distinct values of a low cardinality
    # text column, most frequent first.
    sample_values: list[str] = Field(default_factory=list)


class SchemaForeignKey(ApiModel):
    name: str | None = None
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


class SchemaIndex(ApiModel):
    name: str
    columns: list[str]
    unique: bool


class SchemaObject(ApiModel):
    schema_name: str | None = Field(None, description="The database schema; none on SQLite, MySQL")
    name: str
    kind: ObjectKind
    row_count: int | None = Field(None, description="Rows, documents or keys (an estimate)")
    columns: list[SchemaColumn] = Field(
        default_factory=list,
        description="Columns; for MongoDB the fields seen in sampled documents",
    )
    foreign_keys: list[SchemaForeignKey] = Field(default_factory=list)
    indexes: list[SchemaIndex] = Field(default_factory=list)
    details: dict[str, Any] = Field(
        default_factory=dict, description="Engine specific facts (Redis key types, samples)"
    )

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.name}" if self.schema_name else self.name


class SnapshotSummary(ApiModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    engine: str
    object_count: int
    column_count: int
    duration_ms: float
    trigger: str
    captured_at: datetime


class SnapshotOut(SnapshotSummary):
    objects: list[SchemaObject]
    cached: bool = Field(False, description="Served from the snapshot cache")


class SearchHit(ApiModel):
    object: str
    object_kind: ObjectKind
    match_kind: Literal["object", "column", "index", "foreign_key"]
    name: str
    detail: str = ""


class RelationshipNode(ApiModel):
    id: str
    name: str
    kind: ObjectKind
    row_count: int | None
    columns: list[SchemaColumn]


class RelationshipEdge(ApiModel):
    name: str | None
    source: str
    source_columns: list[str]
    target: str
    target_columns: list[str]


class Relationships(ApiModel):
    snapshot_id: uuid.UUID
    nodes: list[RelationshipNode]
    edges: list[RelationshipEdge]
