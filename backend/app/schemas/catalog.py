"""元数据目录 API 的请求/响应模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SensitivityLevel = Literal["public", "internal", "confidential", "restricted"]


class CatalogTableUpdateRequest(BaseModel):
    """表级人工编辑字段（全部可选，长度/枚举在此约束）。"""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    owner: str | None = Field(default=None, max_length=80)
    business_domain: str | None = Field(default=None, max_length=80)
    sensitivity_level: SensitivityLevel | None = None
    watermark_column: str | None = Field(default=None, max_length=128)


class CatalogColumnUpdateRequest(BaseModel):
    """字段级人工编辑字段（全部可选，长度/枚举在此约束）。"""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    semantic_type: str | None = Field(default=None, max_length=40)
    sensitivity_level: SensitivityLevel | None = None


class CatalogColumnOut(BaseModel):
    id: int
    table_id: int
    column_name: str
    ordinal: int
    display_name: str
    description: str
    data_type: str
    semantic_type: str
    is_nullable: bool
    is_primary_key: bool
    is_foreign_key: bool
    sensitivity_level: str
    status: str
    ontology_column_id: int | None = None
    updated_by: str | None = None
    updated_at: str
    created_at: str


class CatalogTableOut(BaseModel):
    id: int
    source_id: str
    source_name: str = ""
    namespace: str
    table_name: str
    staging_name: str | None = None
    display_name: str
    description: str
    owner: str
    business_domain: str
    sensitivity_level: str
    status: str
    row_count: int
    last_sync_at: str | None = None
    watermark_column: str | None = None
    sync_mode: str
    ontology_term_id: int | None = None
    source_kind: str
    created_at: str
    updated_at: str
    updated_by: str | None = None
    claimed: bool = False
    column_count: int | None = None
    columns: list[CatalogColumnOut] | None = None
    staging_available: bool | None = None
    staging_status: str | None = None
    last_success_at: str | None = None


class CatalogTableListOut(BaseModel):
    items: list[CatalogTableOut]
    total: int
    page: int
    page_size: int


class CatalogChangeOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    source_id: str = ""
    table_id: int | None = None
    column_id: int | None = None
    field_name: str
    old_value: str | None = None
    new_value: str | None = None
    changed_by_role: str
    created_at: str


class CatalogChangeListOut(BaseModel):
    items: list[CatalogChangeOut]
    total: int


class CatalogSummaryOut(BaseModel):
    tables_total: int
    tables_pending: int
    tables_missing: int
    tables_unclaimed: int
    columns_total: int
    columns_pending: int
    sources_total: int
    sources_synced: int
    last_sync_at: str | None = None
    last_catalog_change_at: str | None = None


class CatalogPreviewOut(BaseModel):
    table_id: int
    columns: list[str]
    rows: list[dict[str, Any]]
    masked_columns: list[str]
    sensitive_columns: list[str]
    limit: int
