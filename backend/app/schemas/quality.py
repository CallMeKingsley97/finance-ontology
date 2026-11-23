"""质量规则请求/响应模型（snake_case）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RuleType = Literal["reconciliation", "completeness", "uniqueness", "range", "custom_sql"]
TargetDb = Literal["business", "staging"]
Severity = Literal["info", "warning", "blocker"]
Schedule = Literal["manual", "sync", "report", "sync_report"]


class QualityRuleCreateRequest(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str = Field(min_length=1, max_length=100)
    rule_type: RuleType
    target_db: TargetDb
    target_ref: str = Field(default="", max_length=128)
    expression: dict[str, Any]
    tolerance: float = Field(default=0.01, ge=0, le=1_000_000_000)
    severity: Severity = "warning"
    enabled: bool = True
    schedule: Schedule = "report"
    owner: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=500)


class QualityRuleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    rule_type: RuleType | None = None
    target_db: TargetDb | None = None
    target_ref: str | None = Field(default=None, max_length=128)
    expression: dict[str, Any] | None = None
    tolerance: float | None = Field(default=None, ge=0, le=1_000_000_000)
    severity: Severity | None = None
    enabled: bool | None = None
    schedule: Schedule | None = None
    owner: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class QualityRunRequest(BaseModel):
    period: str | None = Field(default=None, max_length=16)
    rule_codes: list[str] | None = None


class QualityRuleRunRequest(BaseModel):
    period: str | None = Field(default=None, max_length=16)


class QualitySummaryData(BaseModel):
    rules_total: int
    rules_enabled: int
    pass_rate_7d: float | None = None
    runs_7d: int = 0
    failed_results_7d: int = 0
    blocker_failed_7d: int = 0
    last_run_at: str | None = None
    last_run_status: str | None = None


class QualityRuleListData(BaseModel):
    items: list[dict[str, Any]]


class QualityRunListData(BaseModel):
    items: list[dict[str, Any]]
    total: int


class QualityAlertListData(BaseModel):
    items: list[dict[str, Any]]
