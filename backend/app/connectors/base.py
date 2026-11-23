"""连接器统一契约、元数据模型与通用工具。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterator, Protocol


@dataclass(slots=True)
class ColumnMeta:
    """单列元数据。"""

    name: str
    data_type: str
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False
    comment: str | None = None


@dataclass(slots=True)
class ForeignKeyMeta:
    """外键元数据。"""

    from_table: str
    from_column: str
    to_table: str
    to_column: str
    to_schema: str | None = None
    constraint_name: str | None = None


@dataclass(slots=True)
class IndexMeta:
    """索引元数据。"""

    name: str
    columns: list[str]
    unique: bool = False


@dataclass(slots=True)
class TableMeta:
    """表/视图/文件元数据。"""

    schema: str
    name: str
    table_type: str = "table"  # table | view | file
    row_count: int | None = None
    row_count_estimated: bool = False
    comment: str | None = None
    columns: list[ColumnMeta] = field(default_factory=list)
    foreign_keys: list[ForeignKeyMeta] = field(default_factory=list)
    indexes: list[IndexMeta] = field(default_factory=list)


@dataclass(slots=True)
class TestResult:
    """连接测试结果。"""

    success: bool
    latency_ms: int
    server_version: str | None = None
    table_count: int = 0
    latest_period: str | None = None
    message: str = ""


@dataclass(slots=True)
class MetadataSnapshot:
    """一次 inspect 的完整元数据快照。"""

    kind: str
    server_version: str | None
    tables: list[TableMeta]
    truncated: bool = False
    warnings: list[str] = field(default_factory=list)


class ConnectorError(Exception):
    """连接器可读错误；消息必须已脱敏，不包含凭据。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class Connector(Protocol):
    """连接器协议：测试连接、读取元数据、读取样本。"""

    kind: str

    def test(self) -> TestResult: ...

    def inspect(self) -> MetadataSnapshot: ...

    def sample_rows(self, table: TableMeta, limit: int = 5) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class RowReader(Protocol):
    """可选的行读取协议：同步编排按批消费，连接器协议本身保持不变。"""

    def iter_rows(
        self,
        table: TableMeta,
        *,
        watermark_column: str | None,
        watermark_value: str | None,
        batch_size: int,
        max_rows: int,
    ) -> Iterator[list[dict[str, Any]]]: ...


def quote_ident(name: str, dialect: str) -> str:
    """引用标识符；拒绝 NUL，按方言转义引号。"""
    value = str(name or "")
    if not value or "\x00" in value:
        raise ConnectorError("非法标识符")
    if dialect == "mysql":
        return "`" + value.replace("`", "``") + "`"
    return '"' + value.replace('"', '""') + '"'


def json_safe(value: Any) -> Any:
    """把数据库值转换为 JSON 安全值。"""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        try:
            converted = float(value)
        except (ValueError, OverflowError):
            return str(value)
        return converted if math.isfinite(converted) else str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()[:64]
    return str(value)


_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")
_BOOLEAN_WORDS = {"true", "false", "yes", "no", "y", "n", "是", "否"}


def infer_text_type(values: list[str]) -> str:
    """根据文本值推断列类型；空值跳过。"""
    samples = [str(value).strip() for value in values if value is not None and str(value).strip()]
    if not samples:
        return "TEXT"
    if all(_INTEGER_RE.match(value) for value in samples):
        return "INTEGER"
    if all(_NUMBER_RE.match(value) for value in samples):
        return "REAL"
    if all(value.lower() in _BOOLEAN_WORDS for value in samples):
        return "BOOLEAN"
    if all(_is_iso_date(value) for value in samples):
        return "DATE"
    if all(_is_iso_datetime(value) for value in samples):
        return "DATETIME"
    return "TEXT"


def _is_iso_date(value: str) -> bool:
    if len(value) != 10:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_iso_datetime(value: str) -> bool:
    if "T" not in value and " " not in value:
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _snapshot_size(snapshot: MetadataSnapshot) -> int:
    payload = json.dumps(asdict(snapshot), ensure_ascii=False, default=str)
    return len(payload.encode("utf-8"))


def truncate_metadata(
    snapshot: MetadataSnapshot,
    max_tables: int,
    max_columns: int,
    max_bytes: int,
) -> MetadataSnapshot:
    """按表数/列数/JSON 字节上限截断，并记录 warnings。"""
    truncated = snapshot.truncated
    warnings = list(snapshot.warnings)
    tables = snapshot.tables
    if max_tables > 0 and len(tables) > max_tables:
        tables = tables[:max_tables]
        truncated = True
        warnings.append(f"仅展示前 {max_tables} 张表")

    limited: list[TableMeta] = []
    for table in tables:
        columns = table.columns
        if max_columns > 0 and len(columns) > max_columns:
            columns = columns[:max_columns]
            truncated = True
            warnings.append(f"表 {table.name} 仅展示前 {max_columns} 列")
        limited.append(replace(table, columns=columns))

    result = replace(snapshot, tables=limited, truncated=truncated, warnings=warnings)
    budget_warned = False
    while max_bytes > 0 and result.tables and _snapshot_size(result) > max_bytes:
        if not budget_warned:
            result = replace(
                result,
                truncated=True,
                warnings=result.warnings + [f"元数据超过 {max_bytes} 字节，已截断表清单"],
            )
            budget_warned = True
        result = replace(result, tables=result.tables[:-1])
    return result
