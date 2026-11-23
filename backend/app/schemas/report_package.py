"""报表包请求/响应模型（snake_case）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportPackageRequest(BaseModel):
    period: str = Field(min_length=7, max_length=16)
    templates: list[str] | None = None
    comparison: Literal["yoy", "mom", "none"] = "yoy"


class ReportPackageTemplate(BaseModel):
    code: str
    name: str
    description: str
    sections: list[dict[str, str]] = Field(default_factory=list)


class ReportPackageTemplatesData(BaseModel):
    items: list[ReportPackageTemplate]
    periods: list[dict[str, str]] = Field(default_factory=list)


class ReportPackageDocument(BaseModel):
    package_code: str
    title: str
    period: dict[str, str | None]
    generated_at: str
    templates: list[dict[str, Any]]
    quality: dict[str, Any]
    reconciliation: dict[str, Any]
    appendix: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any]
