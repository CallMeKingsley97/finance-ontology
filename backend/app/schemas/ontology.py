"""本体请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TermColumn(BaseModel):
    column_id: int
    table_name: str
    column_name: str
    display_name: str
    description: str
    is_sensitive: bool
    classification: str | None = None
    sample_masked: str | None = None


class SqlAttribute(BaseModel):
    attribute_id: int
    code: str
    name: str
    term_id: int | None = None
    formula_sql: str
    description: str
    unit: str
    certified: bool


class ChangeLogItem(BaseModel):
    change_id: int
    term_id: int
    field_name: str
    old_value: str | None = None
    new_value: str | None = None
    changed_by_role: str
    changed_at: str


class Term(BaseModel):
    term_id: int
    term_code: str
    name: str
    display_name: str
    description: str
    synonyms: list[str] = Field(default_factory=list)
    mapped_table: str | None = None
    mapped_columns: list[str] = Field(default_factory=list)
    category: str
    certified: bool
    certified_by: str | None = None
    certified_at: str | None = None
    source: str
    updated_at: str
    sql_attributes: list[SqlAttribute] = Field(default_factory=list)


class TermDetail(Term):
    columns: list[TermColumn] = Field(default_factory=list)
    change_log: list[ChangeLogItem] = Field(default_factory=list)


class TermListData(BaseModel):
    total: int
    items: list[Term]


class RelationItem(BaseModel):
    relation_id: int
    relation_code: str
    from_term: str
    from_term_code: str
    to_term: str
    to_term_code: str
    relation_type: str
    cardinality: str
    join_hint: str
    description: str


class RelationsData(BaseModel):
    items: list[RelationItem]


class GenerateRequest(BaseModel):
    force: bool = False


class GenerateData(BaseModel):
    status: str
    counts: dict[str, int] = Field(default_factory=dict)
    sensitive_columns: list[str] = Field(default_factory=list)
    version: str
    duration_ms: int = 0


class TermUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    description: str | None = None
    synonyms: list[str] | None = None
    mapped_table: str | None = None
    mapped_columns: list[str] | None = None


class CertifyRequest(BaseModel):
    certified: bool = True
