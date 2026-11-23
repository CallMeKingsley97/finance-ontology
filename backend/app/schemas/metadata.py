"""元数据响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TableSummary(BaseModel):
    name: str
    row_count: int
    column_count: int
    sensitive_columns: list[str] = Field(default_factory=list)
    description: str = ""


class ColumnInfo(BaseModel):
    name: str
    type: str
    pk: bool = False
    notnull: bool = False
    default: str | None = None
    is_sensitive: bool = False
    classification: str | None = None
    sample_masked: str | None = None
    description: str = ""


class ForeignKeyInfo(BaseModel):
    from_column: str
    to_table: str
    to_column: str


class IndexInfo(BaseModel):
    name: str
    unique: bool
    columns: list[str] = Field(default_factory=list)


class TableDetail(BaseModel):
    name: str
    row_count: int
    description: str = ""
    columns: list[ColumnInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)
    indexes: list[IndexInfo] = Field(default_factory=list)
    sample_rows: list[dict] = Field(default_factory=list)
    masked_columns: list[str] = Field(default_factory=list)
