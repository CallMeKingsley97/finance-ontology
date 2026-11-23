"""问数请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChartSeries(BaseModel):
    name: str
    data: list[float] = Field(default_factory=list)


class ChartSpec(BaseModel):
    labels: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)


class Chart(BaseModel):
    type: str = "none"
    spec: ChartSpec = Field(default_factory=ChartSpec)


class QueryColumn(BaseModel):
    name: str
    type: str


class Provenance(BaseModel):
    period_code: str | None = None
    comparison_period: str | None = None
    metric_codes: list[str] = Field(default_factory=list)
    ontology_version: str
    elapsed_ms: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=200)


class QueryAnswer(BaseModel):
    answerable: bool
    answer: str
    intent: str
    engine: str
    sql: str | None = None
    columns: list[QueryColumn] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    chart: Chart = Field(default_factory=Chart)
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None
    masked_columns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
