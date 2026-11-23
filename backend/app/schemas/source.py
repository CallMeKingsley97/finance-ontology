"""数据源请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceItem(BaseModel):
    """数据源列表项；新增字段均有默认值，兼容旧契约。"""

    id: str
    name: str
    kind: str
    path: str
    status: str
    table_count: int
    size_bytes: int
    last_seeded_at: str | None
    description: str = ""
    managed: bool = True
    has_secret: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    original_filename: str | None = None
    last_tested_at: str | None = None
    last_error: str | None = None
    metadata_updated_at: str | None = None


class SourcesData(BaseModel):
    items: list[SourceItem]


class SeedRequest(BaseModel):
    reset: bool = True


class SourceTestRequest(BaseModel):
    path: str | None = None


class SourceTestData(BaseModel):
    success: bool
    latency_ms: int
    sqlite_version: str | None = None
    server_version: str | None = None
    table_count: int = 0
    latest_period: str | None = None
    message: str = ""


class SeedData(BaseModel):
    seeded: bool
    tables: list[str] = Field(default_factory=list)
    rows: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0
    reconciliation: dict | None = None


class DatabaseConfig(BaseModel):
    """数据库源非敏感配置；extra=forbid 防止密码混入 config。"""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    schema_name: str | None = Field(default=None, alias="schema", max_length=128)

    @field_validator("host")
    @classmethod
    def _validate_host(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("host 不能为空")
        if any(char.isspace() for char in text) or any(char in "/?#@" for char in text):
            raise ValueError("host 不能包含空白或 / ? # @")
        return text


class SourceCreateRequest(BaseModel):
    """新增 PostgreSQL/MySQL 源；文件类型走上传接口。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(min_length=1, max_length=32)
    config: DatabaseConfig | None = None
    secret: str | None = Field(default=None, max_length=512)


class SourceTableSummary(BaseModel):
    """元数据表清单项。"""

    model_config = ConfigDict(populate_by_name=True)

    schema_name: str = Field(alias="schema")
    name: str
    table_type: str = "table"
    row_count: int | None = None
    row_count_estimated: bool = False
    column_count: int = 0
    sensitive_columns: list[str] = Field(default_factory=list)
    description: str = ""


class SourceMetadataData(BaseModel):
    source_id: str
    kind: str
    refreshed_at: str
    cached: bool = False
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)
    tables: list[SourceTableSummary] = Field(default_factory=list)


class SourceColumn(BaseModel):
    name: str
    type: str
    pk: bool = False
    notnull: bool = True
    default: str | None = None
    is_sensitive: bool = False
    classification: str | None = None
    sample_masked: Any | None = None
    description: str = ""


class SourceForeignKey(BaseModel):
    from_column: str
    to_schema: str | None = None
    to_table: str
    to_column: str


class SourceIndex(BaseModel):
    name: str
    unique: bool = False
    columns: list[str] = Field(default_factory=list)


class SourceTableDetailData(BaseModel):
    """单表详情；与现有 TableDetail 兼容并扩展。"""

    model_config = ConfigDict(populate_by_name=True)

    source_id: str
    schema_name: str = Field(alias="schema")
    name: str
    table_type: str = "table"
    row_count: int | None = None
    description: str = ""
    comment: str | None = None
    primary_key: list[str] = Field(default_factory=list)
    columns: list[SourceColumn] = Field(default_factory=list)
    foreign_keys: list[SourceForeignKey] = Field(default_factory=list)
    indexes: list[SourceIndex] = Field(default_factory=list)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    masked_columns: list[str] = Field(default_factory=list)
