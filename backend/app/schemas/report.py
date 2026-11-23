"""报告请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportTable(BaseModel):
    columns: list[dict[str, str]] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class ReportChart(BaseModel):
    type: str = "bar"
    x: str = "label"
    labels: list[str] = Field(default_factory=list)
    series: list[dict[str, Any]] = Field(default_factory=list)


class ReportSection(BaseModel):
    key: str
    title: str
    type: str
    data: dict[str, Any] | None = None
    table: ReportTable | None = None
    chart: ReportChart | None = None
    text: str | None = None


class TemplateItem(BaseModel):
    code: str
    name: str
    description: str
    sections: list[dict[str, str]] = Field(default_factory=list)


class TemplatesData(BaseModel):
    items: list[TemplateItem]
    periods: list[dict[str, str]] = Field(default_factory=list)


class GenerateReportRequest(BaseModel):
    template_code: str
    period: str
    comparison: Literal["yoy", "mom", "none"] = "yoy"


class ReportDocument(BaseModel):
    template_code: str
    title: str
    period: dict[str, str | None]
    generated_at: str
    sections: list[ReportSection]
    reconciliation: dict[str, Any]
    appendix: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any]
