"""连接器行读取适配层：参数校验与统一批接口。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.connectors.base import ConnectorError, TableMeta

_MIN_BATCH_SIZE = 1
_MAX_BATCH_SIZE = 5000


def iter_batches(
    connector: Any,
    table: TableMeta,
    *,
    watermark_column: str | None = None,
    watermark_value: str | None = None,
    batch_size: int = 1000,
    max_rows: int = 200_000,
) -> Iterator[list[dict[str, Any]]]:
    """校验参数并委托连接器按批读取；不支持的连接器明确报错。"""
    try:
        safe_batch = int(batch_size)
        safe_max = int(max_rows)
    except (TypeError, ValueError) as exc:
        raise ConnectorError("行读取参数非法") from exc
    if not _MIN_BATCH_SIZE <= safe_batch <= _MAX_BATCH_SIZE:
        raise ConnectorError(f"batch_size 需在 {_MIN_BATCH_SIZE}-{_MAX_BATCH_SIZE} 之间")
    if safe_max <= 0:
        raise ConnectorError("max_rows 必须大于 0")
    reader = getattr(connector, "iter_rows", None)
    if reader is None or not callable(reader):
        raise ConnectorError("连接器不支持行读取")

    watermark = str(watermark_column).strip() if watermark_column else None
    total = 0
    for batch in reader(
        table,
        watermark_column=watermark,
        watermark_value=watermark_value,
        batch_size=safe_batch,
        max_rows=safe_max,
    ):
        if not batch:
            continue
        remaining = safe_max - total
        if remaining <= 0:
            break
        if len(batch) > remaining:
            batch = batch[:remaining]
        total += len(batch)
        yield batch
