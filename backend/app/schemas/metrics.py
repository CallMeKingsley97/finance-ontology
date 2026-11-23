"""指标注册表请求/响应模型（snake_case）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SensitivityLevel = Literal["public", "internal", "confidential", "restricted"]
AliasType = Literal["synonym", "business", "exclusion"]


class MetricCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=100)
    definition: str = Field(default="", max_length=2000)
    formula_text: str = Field(default="", max_length=1000)
    formula_sql: str = Field(default="", max_length=4000)
    boundary: dict[str, str] = Field(default_factory=dict)
    owner: str = Field(default="", max_length=100)
    business_domain: str = Field(default="", max_length=50)
    unit: str = Field(default="CNY", max_length=16)
    sensitivity_level: SensitivityLevel = "internal"
    term_code: str | None = Field(default=None, max_length=64)


class MetricUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    definition: str | None = Field(default=None, max_length=2000)
    formula_text: str | None = Field(default=None, max_length=1000)
    formula_sql: str | None = Field(default=None, max_length=4000)
    boundary: dict[str, str] | None = None
    owner: str | None = Field(default=None, max_length=100)
    business_domain: str | None = Field(default=None, max_length=50)
    unit: str | None = Field(default=None, max_length=16)
    sensitivity_level: SensitivityLevel | None = None
    term_code: str | None = Field(default=None, max_length=64)


class VersionSubmitRequest(BaseModel):
    change_note: str | None = Field(default=None, max_length=500)


class VersionCertifyRequest(BaseModel):
    change_note: str | None = Field(default=None, max_length=500)


class TestRunRequest(BaseModel):
    version: str | None = Field(default=None, max_length=32)


class AliasCreateRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=64)
    alias_type: AliasType
    priority: int = Field(default=0, ge=0, le=1000)


class AliasUpdateRequest(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=64)
    alias_type: AliasType | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    status: Literal["active", "disabled"] | None = None


class MetricSummaryData(BaseModel):
    total: int
    certified: int
    draft: int
    pending: int
    deprecated: int = 0
    tests_total: int = 0
    tests_passed: int = 0
    test_pass_rate: float | None = None


class MetricListData(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


class MetricDetailData(BaseModel):
    code: str
    name: str
    definition: str
    formula_text: str
    formula_sql: str
    boundary: dict[str, str]
    owner: str
    business_domain: str
    unit: str
    sensitivity_level: str
    status: str
    current_version: str | None = None
    term_code: str | None = None
    sort_order: int = 0
    versions: list[dict[str, Any]] = Field(default_factory=list)
    test_cases: list[dict[str, Any]] = Field(default_factory=list)
    aliases: list[dict[str, Any]] = Field(default_factory=list)
    test_summary: dict[str, Any] = Field(default_factory=dict)
    current_certified: dict[str, Any] | None = None
    draft_version: dict[str, Any] | None = None
