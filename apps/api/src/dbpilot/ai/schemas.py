from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from dbpilot.core.schemas import ApiModel
from dbpilot.queries.schemas import SqlTarget

GenerationKind = Literal["sql", "explain", "index_suggestions", "diagnose"]
GenerationStatus = Literal["accepted", "refused", "no_sql", "failed", "answered"]


class GenerateIn(ApiModel):
    connection_id: uuid.UUID
    question: str = Field(min_length=1, max_length=4000)


class GuardOut(ApiModel):
    passed: bool = Field(
        description="True only for exactly one read-only query naming real objects"
    )
    code: str | None = None
    reason: str | None = None


class GenerationOut(ApiModel):
    id: uuid.UUID
    kind: GenerationKind
    status: GenerationStatus
    connection_id: uuid.UUID | None
    prompt: str
    sql: str | None
    explanation: str
    guard: GuardOut
    unknown_tables: list[str] = Field(default_factory=list)
    unknown_columns: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    executable: bool = Field(
        description="Whether it may be run (read-only, through the normal read path). A "
        "refused AI statement never runs: there is no override"
    )
    output: dict[str, Any] = Field(default_factory=dict)
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    schema_snapshot_id: uuid.UUID | None
    actor_label: str
    created_at: datetime


class RunGenerationIn(ApiModel):
    page_size: int = Field(100, ge=1, le=1000)


class ExplainIn(SqlTarget):
    use_model: bool = Field(
        True, description="Ask the model too; false explains from the syntax tree alone"
    )


class AnalysisOut(ApiModel):
    statement: str
    read_only: bool
    refusal: str | None = None
    refusal_code: str | None = None
    tables: list[str] = Field(default_factory=list)
    joins: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    limit: int | None = None
    ctes: list[str] = Field(default_factory=list)
    window_functions: int = 0
    subqueries: int = 0
    columns: list[str] = Field(default_factory=list)


class ExplainOut(ApiModel):
    generation_id: uuid.UUID
    explanation: str
    source: Literal["model", "syntax"]
    model: str
    analysis: AnalysisOut
    executed: bool = Field(False, description="Always false: explaining never runs anything")


class ConnectionIn(ApiModel):
    connection_id: uuid.UUID


class IndexSuggestionOut(ApiModel):
    table: str
    columns: list[str]
    reason: str
    ddl: str = Field(description="For review; run it yourself through the confirmed-write path")
    source: Literal["foreign_key", "query_history"]


class IndexSuggestionsOut(ApiModel):
    generation_id: uuid.UUID
    snapshot_id: uuid.UUID
    analyzed_queries: int
    suggestions: list[IndexSuggestionOut]


class DiagnoseIn(ApiModel):
    connection_id: uuid.UUID
    sql: str = Field(min_length=1, max_length=100_000)
    error: str = Field(min_length=1, max_length=4000)


class DiagnoseOut(ApiModel):
    generation_id: uuid.UUID
    category: str
    explanation: str
    suggestion: str
    similar: list[str]
    model_explanation: str | None = None
