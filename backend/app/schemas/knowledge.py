"""知识库请求/响应模型（snake_case）。

路由仍返回 ``ok(...)`` dict 信封；这些模型用于 OpenAPI 文档与可选校验，
字段与 ``app.services.knowledge.service`` 的返回结构保持一致。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

KnowledgeKind = Literal[
    "term",
    "process",
    "metric_basis",
    "reconciliation_rule",
    "terminology",
    "correction",
]
KnowledgeConfidence = Literal["standard", "opensource", "practice"]
KnowledgeDomain = Literal["accounting_reconciliation", "business_finance_integration"]


class KnowledgeEntryQuery(BaseModel):
    """知识条目列表查询参数。

    仅约束关键字长度与枚举；``page`` / ``page_size`` 保持宽松并在服务层收敛，
    以维持既有契约（``page_size=500`` 返回 100，而非 422）。
    """

    q: str | None = Field(default=None, max_length=100)
    domain: KnowledgeDomain | None = None
    kind: KnowledgeKind | None = None
    source_id: str | None = Field(default=None, max_length=64)
    page: int = 1
    page_size: int = 20


class KnowledgeCoverage(BaseModel):
    ontology_terms_covered: int = 0
    metrics_covered: int = 0
    reconciliation_rules: int = 0
    terminology_phrases: int = 0


class KnowledgeDomainCount(BaseModel):
    domain: str
    entries: int


class KnowledgeKindCount(BaseModel):
    kind: str
    entries: int


class KnowledgeSummary(BaseModel):
    available: bool
    pack_version: str = ""
    entries: int = 0
    sources: int = 0
    loaded_at: str | None = None
    last_error: str | None = None
    domains: list[KnowledgeDomainCount] = Field(default_factory=list)
    kinds: list[KnowledgeKindCount] = Field(default_factory=list)
    coverage: KnowledgeCoverage = Field(default_factory=KnowledgeCoverage)


class KnowledgeEntryItem(BaseModel):
    entry_id: str
    kind: str
    domain: str
    title: str
    statement: str
    basis: str
    source_id: str
    source_name: str = ""
    source_type: str = ""
    version: str
    effective_from: str
    confidence: str


class KnowledgeEntryList(BaseModel):
    items: list[KnowledgeEntryItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class KnowledgeLink(BaseModel):
    target_kind: str
    target_code: str


class KnowledgeSourceRef(BaseModel):
    source_id: str = ""
    name: str = ""
    type: str = ""
    license: str = ""
    url: str = ""
    notice: str = ""
    version: str = ""


class KnowledgeEntryDetail(KnowledgeEntryItem):
    payload: dict[str, Any] = Field(default_factory=dict)
    links: list[KnowledgeLink] = Field(default_factory=list)
    source: KnowledgeSourceRef = Field(default_factory=KnowledgeSourceRef)


class KnowledgeSourceItem(BaseModel):
    source_id: str
    name: str
    type: str
    license: str = ""
    url: str = ""
    notice: str = ""
    version: str = ""
    entry_count: int = 0


class KnowledgeSourceList(BaseModel):
    items: list[KnowledgeSourceItem] = Field(default_factory=list)


class KnowledgeReloadResult(BaseModel):
    reloaded: bool = True
    pack_version: str = ""
    entries: int = 0
    sources: int = 0
    domains: int = 0
    duration_ms: int = 0
    loaded_at: str | None = None


class KnowledgeViolation(BaseModel):
    file: str = ""
    entry_id: str | None = None
    field: str = ""
    message: str = ""
