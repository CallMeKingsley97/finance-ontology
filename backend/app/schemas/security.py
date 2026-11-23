"""安全与审计响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RoleItem(BaseModel):
    code: str
    name: str
    description: str
    permissions: list[str] = Field(default_factory=list)
    masking: str


class RolesData(BaseModel):
    items: list[RoleItem]


class SensitiveColumnPolicy(BaseModel):
    table: str
    column: str
    classification: str
    description: str
    masking: dict[str, str]


class PoliciesData(BaseModel):
    sensitive_columns: list[SensitiveColumnPolicy]
    audit_fields: list[str]


class AuditItem(BaseModel):
    audit_id: int
    question: str
    intent: str | None = None
    role: str
    sql_preview: str | None = None
    row_count: int = 0
    status: str
    masked_columns: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    created_at: str


class AuditData(BaseModel):
    items: list[AuditItem]
    total: int
