"""数据源元数据编排：快照缓存、表清单、单表详情与外部敏感列脱敏。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from app.connectors import (
    ColumnMeta,
    ConnectorError,
    ForeignKeyMeta,
    IndexMeta,
    MetadataSnapshot,
    TableMeta,
    truncate_metadata,
)
from app.core.config import settings
from app.core.crypto import sanitize_error
from app.db.registry import get_source_row, update_source_row
from app.schemas.common import AppError
from app.services.metadata import get_table_detail, list_tables
from app.services.security import EXTERNAL_SENSITIVE_COLUMNS, mask_rows, mask_value
from app.services.sources import (
    BUILTIN_SOURCE_ID,
    DATABASE_KINDS,
    _connector_for_row as connector_for_row,
)

_CLASSIFICATIONS = {
    "phone": "PII_PHONE",
    "mobile": "PII_PHONE",
    "bank_account": "PII_BANK_ACCOUNT",
    "id_card": "PII_ID_CARD",
    "email": "PII_EMAIL",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def external_sensitive_columns(columns: list[str]) -> list[str]:
    """返回命中外部敏感列名的列（保持原大小写）。"""
    return [name for name in columns if name.lower() in EXTERNAL_SENSITIVE_COLUMNS]


def _classification(column: str) -> str | None:
    return _CLASSIFICATIONS.get(column.lower())


def snapshot_to_table_summaries(snapshot: MetadataSnapshot) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for table in snapshot.tables:
        names = [column.name for column in table.columns]
        items.append(
            {
                "schema": table.schema,
                "name": table.name,
                "table_type": table.table_type,
                "row_count": table.row_count,
                "row_count_estimated": table.row_count_estimated,
                "column_count": len(names),
                "sensitive_columns": external_sensitive_columns(names),
                "description": table.comment or "",
            }
        )
    return items


def _snapshot_from_dict(data: dict[str, Any]) -> MetadataSnapshot:
    tables: list[TableMeta] = []
    for item in data.get("tables") or []:
        columns = [ColumnMeta(**column) for column in item.get("columns") or []]
        foreign_keys = [
            ForeignKeyMeta(**foreign_key) for foreign_key in item.get("foreign_keys") or []
        ]
        indexes = [IndexMeta(**index) for index in item.get("indexes") or []]
        tables.append(
            TableMeta(
                schema=str(item.get("schema") or ""),
                name=str(item.get("name") or ""),
                table_type=str(item.get("table_type") or "table"),
                row_count=item.get("row_count"),
                row_count_estimated=bool(item.get("row_count_estimated")),
                comment=item.get("comment"),
                columns=columns,
                foreign_keys=foreign_keys,
                indexes=indexes,
            )
        )
    return MetadataSnapshot(
        kind=str(data.get("kind") or ""),
        server_version=data.get("server_version"),
        tables=tables,
        truncated=bool(data.get("truncated")),
        warnings=list(data.get("warnings") or []),
    )


def _load_snapshot(row: dict[str, Any]) -> MetadataSnapshot | None:
    raw = row.get("metadata_json")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return _snapshot_from_dict(data)
    except (TypeError, ValueError):
        return None


def _is_stale(row: dict[str, Any]) -> bool:
    """数据库源按 TTL 判断缓存是否过期；文件源上传后不变。"""
    if row.get("kind") not in DATABASE_KINDS:
        return False
    ttl = int(settings.source_metadata_ttl_seconds or 0)
    if ttl <= 0:
        return False
    updated = row.get("metadata_updated_at")
    if not updated:
        return True
    try:
        parsed = datetime.fromisoformat(str(updated))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return datetime.now().astimezone() - parsed > timedelta(seconds=ttl)


def _snapshot_json(snapshot: MetadataSnapshot) -> str:
    return json.dumps(asdict(snapshot), ensure_ascii=False, default=str)


def _refresh_snapshot(source_id: str, row: dict[str, Any]) -> MetadataSnapshot:
    try:
        connector = connector_for_row(row)
    except AppError:
        raise
    except ConnectorError as exc:
        raise AppError(
            422,
            "SOURCE_UNAVAILABLE",
            "元数据读取失败",
            {"error": sanitize_error(str(exc))},
        ) from exc
    except Exception as exc:
        raise AppError(
            422,
            "SOURCE_UNAVAILABLE",
            "元数据读取失败",
            {"error": sanitize_error(str(exc))},
        ) from exc
    try:
        snapshot = connector.inspect()
    except ConnectorError as exc:
        raise AppError(
            422,
            "SOURCE_UNAVAILABLE",
            "元数据读取失败",
            {"error": sanitize_error(str(exc))},
        ) from exc
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            422,
            "SOURCE_UNAVAILABLE",
            "元数据读取失败",
            {"error": sanitize_error(str(exc))},
        ) from exc
    finally:
        connector.close()
    snapshot = truncate_metadata(
        snapshot,
        settings.source_max_tables,
        settings.source_max_columns,
        settings.source_max_metadata_bytes,
    )
    update_source_row(
        source_id,
        metadata_json=_snapshot_json(snapshot),
        metadata_updated_at=_now(),
        table_count=len(snapshot.tables),
    )
    return snapshot


def _builtin_metadata() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in list_tables()["items"]:
        items.append(
            {
                "schema": "main",
                "name": item["name"],
                "table_type": "table",
                "row_count": item["row_count"],
                "row_count_estimated": False,
                "column_count": item["column_count"],
                "sensitive_columns": list(item["sensitive_columns"]),
                "description": item.get("description") or "",
            }
        )
    return {
        "source_id": BUILTIN_SOURCE_ID,
        "kind": "sqlite",
        "refreshed_at": _now(),
        "cached": False,
        "truncated": False,
        "warnings": [],
        "tables": items,
    }


def list_source_metadata(source_id: str, refresh: bool = False) -> dict[str, Any]:
    """返回源元数据表清单；数据库源按 TTL 自动刷新。"""
    if source_id == BUILTIN_SOURCE_ID:
        return _builtin_metadata()
    row = get_source_row(source_id)
    if row is None:
        raise AppError(404, "SOURCE_NOT_FOUND", f"数据源不存在: {source_id}")

    snapshot = _load_snapshot(row)
    cached = snapshot is not None
    if snapshot is None or refresh or _is_stale(row):
        snapshot = _refresh_snapshot(source_id, row)
        cached = False
        refreshed_at = _now()
    else:
        refreshed_at = str(row.get("metadata_updated_at") or _now())

    return {
        "source_id": source_id,
        "kind": row["kind"],
        "refreshed_at": refreshed_at,
        "cached": cached,
        "truncated": snapshot.truncated,
        "warnings": list(snapshot.warnings),
        "tables": snapshot_to_table_summaries(snapshot),
    }


def _find_table(snapshot: MetadataSnapshot, table: str, schema: str | None) -> TableMeta:
    for item in snapshot.tables:
        if item.name != table:
            continue
        if schema and item.schema != schema:
            continue
        return item
    raise AppError(404, "TABLE_NOT_FOUND", f"表不存在: {table}")


def _builtin_table_detail(table: str, role: str) -> dict[str, Any]:
    detail = get_table_detail(table, role)
    primary_key = [column["name"] for column in detail["columns"] if column.get("pk")]
    return {
        **detail,
        "source_id": BUILTIN_SOURCE_ID,
        "schema": "main",
        "table_type": "table",
        "comment": detail.get("description") or None,
        "primary_key": primary_key,
    }


def get_source_table_detail(
    source_id: str,
    table: str,
    schema: str | None,
    role: str,
    limit: int = 5,
) -> dict[str, Any]:
    """返回单表结构、主外键、索引与按角色脱敏的样本。"""
    if source_id == BUILTIN_SOURCE_ID:
        return _builtin_table_detail(table, role)

    row = get_source_row(source_id)
    if row is None:
        raise AppError(404, "SOURCE_NOT_FOUND", f"数据源不存在: {source_id}")
    snapshot = _load_snapshot(row)
    if snapshot is None or _is_stale(row):
        snapshot = _refresh_snapshot(source_id, row)

    target = _find_table(snapshot, table, schema)
    safe_limit = max(1, min(int(limit or 5), max(1, settings.source_sample_limit)))

    try:
        connector = connector_for_row(row)
    except AppError:
        raise
    except ConnectorError as exc:
        raise AppError(
            422,
            "SOURCE_UNAVAILABLE",
            "元数据读取失败",
            {"error": sanitize_error(str(exc))},
        ) from exc
    except Exception as exc:
        raise AppError(
            422,
            "SOURCE_UNAVAILABLE",
            "元数据读取失败",
            {"error": sanitize_error(str(exc))},
        ) from exc
    try:
        rows = connector.sample_rows(target, safe_limit)
    except ConnectorError as exc:
        raise AppError(
            422,
            "SOURCE_UNAVAILABLE",
            "样本读取失败",
            {"error": sanitize_error(str(exc))},
        ) from exc
    except Exception as exc:
        raise AppError(
            422,
            "SOURCE_UNAVAILABLE",
            "样本读取失败",
            {"error": sanitize_error(str(exc))},
        ) from exc
    finally:
        connector.close()

    column_names = [column.name for column in target.columns]
    sensitive = external_sensitive_columns(column_names)
    sample_masked_by_column: dict[str, str | None] = {}
    for column in target.columns:
        if column.name.lower() in EXTERNAL_SENSITIVE_COLUMNS:
            raw = next((item.get(column.name) for item in rows if item.get(column.name) is not None), None)
            sample_masked_by_column[column.name] = mask_value(column.name, raw, role)
        else:
            sample_masked_by_column[column.name] = None

    masked_rows, masked_columns = mask_rows(column_names, rows, role, sensitive)
    columns = [
        {
            "name": column.name,
            "type": column.data_type,
            "pk": column.is_primary_key,
            "notnull": not column.nullable,
            "default": column.default,
            "is_sensitive": column.name.lower() in EXTERNAL_SENSITIVE_COLUMNS,
            "classification": _classification(column.name),
            "sample_masked": sample_masked_by_column.get(column.name),
            "description": column.comment or "",
        }
        for column in target.columns
    ]
    return {
        "source_id": source_id,
        "schema": target.schema,
        "name": target.name,
        "table_type": target.table_type,
        "row_count": target.row_count,
        "description": target.comment or "",
        "comment": target.comment,
        "primary_key": [column.name for column in target.columns if column.is_primary_key],
        "columns": columns,
        "foreign_keys": [
            {
                "from_column": foreign_key.from_column,
                "to_schema": foreign_key.to_schema,
                "to_table": foreign_key.to_table,
                "to_column": foreign_key.to_column,
            }
            for foreign_key in target.foreign_keys
        ],
        "indexes": [
            {
                "name": index.name,
                "unique": index.unique,
                "columns": list(index.columns),
            }
            for index in target.indexes
        ],
        "sample_rows": masked_rows,
        "masked_columns": masked_columns,
    }
