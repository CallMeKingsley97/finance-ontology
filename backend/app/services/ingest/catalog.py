"""元数据目录服务：内置/外部源骨架登记、人工编辑、变更留痕与脱敏预览。

设计要点：
- 自动登记只更新系统字段（staging 名、行数、最近同步、类型等），
  ``display_name``/``description``/``owner``/``business_domain``/``sensitivity_level``
  等人工字段永不被同步覆盖；
- 人工编辑与变更日志在同一 staging 事务内提交；
- 全部 SQL 参数化，动态列名仅来自常量白名单，禁止 ``SELECT *``。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from app.connectors.base import ConnectorError, MetadataSnapshot, json_safe, quote_ident
from app.core.config import settings
from app.db.connection import BUSINESS_TABLES, connect, quote_identifier
from app.db.registry import list_source_rows
from app.schemas.common import AppError
from app.services.ingest import store, writers
from app.services.metadata import COLUMN_DESCRIPTIONS, TABLE_DESCRIPTIONS
from app.services.security import EXTERNAL_SENSITIVE_COLUMNS, SENSITIVE_BY_TABLE, mask_rows
from app.services.source_metadata import _snapshot_from_dict

logger = logging.getLogger("finance_ontology.ingest.catalog")

_SENSITIVITY_LEVELS = ("public", "internal", "confidential", "restricted")
_CATALOG_STATUSES = ("pending", "active", "missing")
_TABLE_EDITABLE: dict[str, int] = {
    "display_name": 80,
    "description": 500,
    "owner": 80,
    "business_domain": 80,
    "sensitivity_level": 0,
    "watermark_column": 128,
}
_COLUMN_EDITABLE: dict[str, int] = {
    "display_name": 80,
    "description": 500,
    "semantic_type": 40,
    "sensitivity_level": 0,
}
_UNCLAIMED = "待认领"

_TABLE_COLUMNS_SQL = (
    "id, source_id, namespace, table_name, staging_name, display_name, description, "
    "owner, business_domain, sensitivity_level, status, row_count, last_sync_at, "
    "watermark_column, sync_mode, ontology_term_id, source_kind, created_at, updated_at, updated_by"
)
_COLUMN_COLUMNS_SQL = (
    "id, table_id, column_name, ordinal, display_name, description, data_type, "
    "semantic_type, is_nullable, is_primary_key, is_foreign_key, sensitivity_level, "
    "status, ontology_column_id, updated_by, updated_at, created_at"
)


def _now() -> str:
    return store.staging_now()


def _meta_get(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM catalog_meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else ""


def _meta_set(conn: sqlite3.Connection, key: str, value: str, now: str) -> None:
    conn.execute(
        """INSERT INTO catalog_meta (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, value, now),
    )


def _source_map() -> dict[str, dict[str, str]]:
    try:
        return {
            str(row["id"]): {"name": str(row.get("name") or ""), "kind": str(row.get("kind") or "")}
            for row in list_source_rows()
        }
    except Exception:  # noqa: BLE001 - 注册库不可用时目录仍可读
        logger.exception("registry unavailable for catalog source map")
        return {}


def _ontology_maps() -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """读取业务库中的本体映射；业务库缺失时返回空映射。"""
    try:
        conn = connect(read_only=True, enforce_authorizer=False)
    except Exception:  # noqa: BLE001
        return {}, {}
    try:
        term_map: dict[str, int] = {}
        for row in conn.execute(
            "SELECT term_id, mapped_table FROM ontology_term "
            "WHERE mapped_table IS NOT NULL AND mapped_table <> '' ORDER BY term_id"
        ):
            term_map.setdefault(str(row["mapped_table"]), int(row["term_id"]))
        column_map: dict[tuple[str, str], int] = {}
        for row in conn.execute(
            "SELECT column_id, table_name, column_name FROM ontology_term_column ORDER BY column_id"
        ):
            column_map.setdefault(
                (str(row["table_name"]), str(row["column_name"])), int(row["column_id"])
            )
        return term_map, column_map
    except Exception:  # noqa: BLE001
        logger.exception("ontology mapping read failed")
        return {}, {}
    finally:
        conn.close()


def _staging_row_counts(conn: sqlite3.Connection, source_id: str) -> dict[tuple[str, str], int]:
    rows = conn.execute(
        "SELECT namespace, table_name, row_count FROM staging_table WHERE source_id = ?",
        (source_id,),
    ).fetchall()
    return {(str(row["namespace"]), str(row["table_name"])): int(row["row_count"]) for row in rows}


def _upsert_table(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    namespace: str,
    table_name: str,
    staging_name: str | None,
    description: str,
    sensitivity_level: str,
    status: str,
    row_count: int,
    last_sync_at: str | None,
    sync_mode: str,
    ontology_term_id: int | None,
    source_kind: str,
    now: str,
) -> int:
    row = conn.execute(
        """INSERT INTO catalog_table
           (source_id, namespace, table_name, staging_name, display_name, description, owner,
            business_domain, sensitivity_level, status, row_count, last_sync_at, watermark_column,
            sync_mode, ontology_term_id, source_kind, created_at, updated_at)
           VALUES (?, ?, ?, ?, '', ?, ?, '', ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
           ON CONFLICT(source_id, namespace, table_name) DO UPDATE SET
               staging_name = excluded.staging_name,
               row_count = excluded.row_count,
               last_sync_at = COALESCE(excluded.last_sync_at, catalog_table.last_sync_at),
               source_kind = excluded.source_kind,
               sync_mode = CASE
                   WHEN catalog_table.watermark_column IS NOT NULL
                        AND TRIM(catalog_table.watermark_column) <> ''
                   THEN 'incremental' ELSE 'full' END,
               status = CASE WHEN catalog_table.status = 'missing' THEN 'pending'
                             ELSE catalog_table.status END,
               description = CASE
                   WHEN catalog_table.updated_by IS NULL AND catalog_table.description = ''
                   THEN excluded.description ELSE catalog_table.description END,
               ontology_term_id = COALESCE(catalog_table.ontology_term_id, excluded.ontology_term_id),
               updated_at = excluded.updated_at
           RETURNING id""",
        (
            source_id,
            namespace,
            table_name,
            staging_name,
            description,
            _UNCLAIMED,
            sensitivity_level,
            status,
            row_count,
            last_sync_at,
            sync_mode,
            ontology_term_id,
            source_kind,
            now,
            now,
        ),
    ).fetchone()
    return int(row["id"])


def _upsert_column(
    conn: sqlite3.Connection,
    *,
    table_id: int,
    column_name: str,
    ordinal: int,
    description: str,
    data_type: str,
    is_nullable: bool,
    is_primary_key: bool,
    is_foreign_key: bool,
    sensitivity_level: str,
    ontology_column_id: int | None,
    now: str,
) -> int:
    row = conn.execute(
        """INSERT INTO catalog_column
           (table_id, column_name, ordinal, display_name, description, data_type, semantic_type,
            is_nullable, is_primary_key, is_foreign_key, sensitivity_level, status,
            ontology_column_id, updated_by, updated_at, created_at)
           VALUES (?, ?, ?, '', ?, ?, '', ?, ?, ?, ?, 'pending', ?, NULL, ?, ?)
           ON CONFLICT(table_id, column_name) DO UPDATE SET
               ordinal = excluded.ordinal,
               data_type = excluded.data_type,
               is_nullable = excluded.is_nullable,
               is_primary_key = excluded.is_primary_key,
               is_foreign_key = excluded.is_foreign_key,
               status = CASE WHEN catalog_column.status = 'missing' THEN 'pending'
                             ELSE catalog_column.status END,
               description = CASE
                   WHEN catalog_column.updated_by IS NULL AND catalog_column.description = ''
                   THEN excluded.description ELSE catalog_column.description END,
               ontology_column_id = COALESCE(catalog_column.ontology_column_id,
                                             excluded.ontology_column_id),
               updated_at = excluded.updated_at
           RETURNING id""",
        (
            table_id,
            column_name,
            ordinal,
            description,
            data_type,
            int(is_nullable),
            int(is_primary_key),
            int(is_foreign_key),
            sensitivity_level,
            ontology_column_id,
            now,
            now,
        ),
    ).fetchone()
    return int(row["id"])


def _mark_missing_columns(
    conn: sqlite3.Connection, table_id: int, present: set[str], now: str
) -> int:
    rows = conn.execute(
        "SELECT id, column_name FROM catalog_column WHERE table_id = ?", (table_id,)
    ).fetchall()
    missing = [(now, int(row["id"])) for row in rows if str(row["column_name"]) not in present]
    if missing:
        conn.executemany(
            "UPDATE catalog_column SET status = 'missing', updated_at = ? WHERE id = ?",
            missing,
        )
    return len(missing)


def _apply_snapshot(
    conn: sqlite3.Connection,
    source_id: str,
    source_kind: str,
    snapshot: MetadataSnapshot,
    now: str,
    *,
    last_sync_at: str | None,
) -> dict[str, int]:
    """按快照 upsert 目录骨架并标记缺失；不触碰人工字段。"""
    term_map, column_map = _ontology_maps()
    row_counts = _staging_row_counts(conn, source_id)
    present_tables: set[tuple[str, str]] = set()
    tables = 0
    columns = 0
    for table in snapshot.tables:
        if table.table_type not in ("table", "file"):
            continue
        namespace = table.schema or ""
        table_name = table.name
        present_tables.add((namespace, table_name))
        staging_name = writers.staging_name_for(source_id, namespace, table_name)
        sensitivity = "internal"
        table_id = _upsert_table(
            conn,
            source_id=source_id,
            namespace=namespace,
            table_name=table_name,
            staging_name=staging_name,
            description=table.comment or "",
            sensitivity_level=sensitivity,
            status="pending",
            row_count=int(row_counts.get((namespace, table_name), table.row_count or 0)),
            last_sync_at=last_sync_at,
            sync_mode="full",
            ontology_term_id=term_map.get(table_name),
            source_kind=source_kind,
            now=now,
        )
        tables += 1
        foreign_columns = {fk.from_column for fk in table.foreign_keys}
        present_columns: set[str] = set()
        for ordinal, column in enumerate(table.columns):
            present_columns.add(column.name)
            if (table_name, column.name) in SENSITIVE_BY_TABLE:
                column_sensitivity = "restricted"
            elif column.name.lower() in EXTERNAL_SENSITIVE_COLUMNS:
                column_sensitivity = "confidential"
            else:
                column_sensitivity = "internal"
            _upsert_column(
                conn,
                table_id=table_id,
                column_name=column.name,
                ordinal=ordinal,
                description=column.comment or "",
                data_type=column.data_type or "TEXT",
                is_nullable=bool(column.nullable),
                is_primary_key=bool(column.is_primary_key),
                is_foreign_key=column.name in foreign_columns,
                sensitivity_level=column_sensitivity,
                ontology_column_id=column_map.get((table_name, column.name)),
                now=now,
            )
            columns += 1
        _mark_missing_columns(conn, table_id, present_columns, now)
    missing_tables = mark_missing_tables(source_id, present_tables, conn=conn, now=now)
    return {"tables": tables, "columns": columns, "missing_tables": missing_tables}


def _builtin_guard_value() -> str:
    path = settings.db_path
    try:
        mtime = path.stat().st_mtime_ns if path.exists() else 0
    except OSError:
        mtime = 0
    return f"{mtime}:{len(BUSINESS_TABLES)}"


def _bootstrap_builtin(conn: sqlite3.Connection, now: str) -> dict[str, int]:
    guard = _builtin_guard_value()
    if _meta_get(conn, "builtin") == guard:
        return {"tables": 0, "columns": 0}
    term_map, column_map = _ontology_maps()
    tables = 0
    columns = 0
    try:
        finance = connect(read_only=True, enforce_authorizer=False)
    except Exception:  # noqa: BLE001 - 业务库缺失不阻断目录初始化
        logger.warning("builtin catalog skipped: finance database unavailable")
        _meta_set(conn, "builtin", guard, now)
        return {"tables": 0, "columns": 0}
    try:
        for table_name in BUSINESS_TABLES:
            quoted = quote_identifier(table_name)
            table_info = finance.execute(f"PRAGMA table_info({quoted})").fetchall()
            count_row = finance.execute(f"SELECT COUNT(*) AS n FROM {quoted}").fetchone()
            table_id = _upsert_table(
                conn,
                source_id="sqlite-local",
                namespace="main",
                table_name=table_name,
                staging_name=writers.staging_name_for("sqlite-local", "main", table_name),
                description=TABLE_DESCRIPTIONS.get(table_name, ""),
                sensitivity_level="internal",
                status="pending",
                row_count=int(count_row["n"]) if count_row else 0,
                last_sync_at=None,
                sync_mode="full",
                ontology_term_id=term_map.get(table_name),
                source_kind="sqlite",
                now=now,
            )
            tables += 1
            for ordinal, column in enumerate(table_info):
                column_name = str(column["name"])
                sensitivity = (
                    "restricted"
                    if (table_name, column_name) in SENSITIVE_BY_TABLE
                    else "internal"
                )
                _upsert_column(
                    conn,
                    table_id=table_id,
                    column_name=column_name,
                    ordinal=ordinal,
                    description=COLUMN_DESCRIPTIONS.get(f"{table_name}.{column_name}", ""),
                    data_type=str(column["type"] or "TEXT"),
                    is_nullable=not bool(column["notnull"]),
                    is_primary_key=bool(column["pk"]),
                    is_foreign_key=False,
                    sensitivity_level=sensitivity,
                    ontology_column_id=column_map.get((table_name, column_name)),
                    now=now,
                )
                columns += 1
    except Exception:  # noqa: BLE001
        logger.exception("builtin catalog bootstrap failed")
    finally:
        finance.close()
    _meta_set(conn, "builtin", guard, now)
    return {"tables": tables, "columns": columns}


def ensure_bootstrap() -> dict[str, Any]:
    """幂等初始化内置 10 表与外部源目录骨架；外部源只读快照，不建远程连接。"""
    store.ensure_staging_available()
    now = _now()
    counts = {"builtin_tables": 0, "builtin_columns": 0, "source_tables": 0, "source_columns": 0}
    with store.staging_connection() as conn:
        builtin = _bootstrap_builtin(conn, now)
        counts["builtin_tables"] = builtin["tables"]
        counts["builtin_columns"] = builtin["columns"]

        for row in list_source_rows():
            source_id = str(row["id"])
            guard = str(row.get("metadata_updated_at") or "")
            meta_key = f"source:{source_id}"
            if guard and _meta_get(conn, meta_key) == guard:
                continue
            raw = row.get("metadata_json")
            if not raw:
                _meta_set(conn, meta_key, guard, now)
                continue
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("metadata snapshot is not an object")
                snapshot = _snapshot_from_dict(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("catalog skip source=%s: %s", source_id, exc)
                _meta_set(conn, meta_key, guard, now)
                continue
            result = _apply_snapshot(
                conn,
                source_id,
                str(row.get("kind") or ""),
                snapshot,
                now,
                last_sync_at=None,
            )
            counts["source_tables"] += result["tables"]
            counts["source_columns"] += result["columns"]
            _meta_set(conn, meta_key, guard, now)
    return counts


def mark_missing_tables(
    source_id: str,
    present: set[tuple[str, str]],
    *,
    conn: sqlite3.Connection | None = None,
    now: str | None = None,
) -> int:
    """把本次快照中未出现的表（及其列）标记为 missing，保留历史与人工说明。"""
    timestamp = now or _now()
    if conn is None:
        store.ensure_staging_available()
        with store.staging_connection() as own_conn:
            return mark_missing_tables(source_id, present, conn=own_conn, now=timestamp)
    rows = conn.execute(
        "SELECT id, namespace, table_name FROM catalog_table WHERE source_id = ?",
        (source_id,),
    ).fetchall()
    missing_ids = [
        int(row["id"])
        for row in rows
        if (str(row["namespace"]), str(row["table_name"])) not in present
    ]
    if not missing_ids:
        return 0
    conn.executemany(
        "UPDATE catalog_table SET status = 'missing', updated_at = ? WHERE id = ?",
        [(timestamp, table_id) for table_id in missing_ids],
    )
    conn.executemany(
        "UPDATE catalog_column SET status = 'missing', updated_at = ? WHERE table_id = ?",
        [(timestamp, table_id) for table_id in missing_ids],
    )
    return len(missing_ids)


def sync_source_catalog(
    source_id: str, snapshot: MetadataSnapshot, run_id: str | None = None
) -> dict[str, Any]:
    """同步结束后登记/更新目录骨架；不覆盖任何人工字段，不写变更日志。"""
    del run_id  # 保留契约参数，用于审计关联（B1 不改目录行）
    store.ensure_staging_available()
    sources = _source_map()
    source_kind = sources.get(source_id, {}).get("kind", "")
    now = _now()
    with store.staging_connection() as conn:
        result = _apply_snapshot(
            conn, source_id, source_kind, snapshot, now, last_sync_at=now
        )
    return {"source_id": source_id, **result}


def sync_settings(source_id: str, namespace: str, table_name: str) -> dict[str, Any]:
    """返回水位配置；人工配置以 catalog_table 为准，水位值以 staging_table 为准。"""
    store.ensure_staging_available()
    with store.staging_connection() as conn:
        row = conn.execute(
            """SELECT c.watermark_column AS catalog_watermark,
                      s.watermark_value AS watermark_value,
                      s.sync_mode AS staging_mode
               FROM catalog_table c
               LEFT JOIN staging_table s
                 ON s.source_id = c.source_id
                AND s.namespace = c.namespace
                AND s.table_name = c.table_name
               WHERE c.source_id = ? AND c.namespace = ? AND c.table_name = ?""",
            (source_id, namespace, table_name),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """SELECT watermark_column, watermark_value, sync_mode
                   FROM staging_table
                   WHERE source_id = ? AND namespace = ? AND table_name = ?""",
                (source_id, namespace, table_name),
            ).fetchone()
    if row is None:
        return {"watermark_column": None, "watermark_value": None, "sync_mode": "full"}
    watermark_column = row["catalog_watermark"] if "catalog_watermark" in row.keys() else row["watermark_column"]
    watermark_value = row["watermark_value"]
    sync_mode = "incremental" if watermark_column else "full"
    return {
        "watermark_column": str(watermark_column) if watermark_column else None,
        "watermark_value": str(watermark_value) if watermark_value is not None else None,
        "sync_mode": sync_mode,
    }


def _validate_filter(value: str | None, allowed: tuple[str, ...], label: str) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text not in allowed:
        raise AppError(422, "VALIDATION_FAILED", f"{label} 筛选值非法")
    return text


def list_tables(filters: dict[str, Any]) -> dict[str, Any]:
    store.ensure_staging_available()
    conditions: list[str] = []
    params: list[Any] = []
    source_id = str(filters.get("source_id") or "").strip()
    if source_id:
        conditions.append("source_id = ?")
        params.append(source_id)
    status = _validate_filter(filters.get("status"), _CATALOG_STATUSES, "status")
    if status:
        conditions.append("status = ?")
        params.append(status)
    owner = str(filters.get("owner") or "").strip()
    if owner:
        conditions.append("owner = ?")
        params.append(owner)
    domain = str(filters.get("business_domain") or "").strip()
    if domain:
        conditions.append("business_domain = ?")
        params.append(domain)
    sensitivity = _validate_filter(
        filters.get("sensitivity_level"), _SENSITIVITY_LEVELS, "sensitivity_level"
    )
    if sensitivity:
        conditions.append("sensitivity_level = ?")
        params.append(sensitivity)
    query_text = str(filters.get("q") or "").strip()
    if query_text:
        conditions.append(
            "instr(lower(COALESCE(display_name, '') || ' ' || table_name), lower(?)) > 0"
        )
        params.append(query_text)

    try:
        page = max(1, int(filters.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(filters.get("page_size") or 20)
    except (TypeError, ValueError):
        page_size = 20
    page_size = max(1, min(page_size, 100))
    offset = min((page - 1) * page_size, 10_000)

    where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    with store.staging_connection() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM catalog_table{where_sql}", tuple(params)
        ).fetchone()
        rows = conn.execute(
            f"""SELECT {_TABLE_COLUMNS_SQL},
                       (SELECT COUNT(*) FROM catalog_column cc
                         WHERE cc.table_id = catalog_table.id
                           AND cc.status <> 'missing') AS column_count
                FROM catalog_table{where_sql}
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?""",
            tuple([*params, page_size, offset]),
        ).fetchall()
    sources = _source_map()
    items = []
    for row in rows:
        item = dict(row)
        meta = sources.get(str(row["source_id"]), {})
        item["source_name"] = meta.get("name", "")
        item["claimed"] = item.get("owner") != _UNCLAIMED
        items.append(item)
    return {
        "items": items,
        "total": int(total_row["n"]) if total_row else 0,
        "page": page,
        "page_size": page_size,
    }


def _fetch_table_row(conn: sqlite3.Connection, table_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT {_TABLE_COLUMNS_SQL} FROM catalog_table WHERE id = ?", (table_id,)
    ).fetchone()
    if row is None:
        raise AppError(404, "CATALOG_TABLE_NOT_FOUND", f"目录表不存在: {table_id}")
    return row


def get_table(table_id: int) -> dict[str, Any]:
    store.ensure_staging_available()
    with store.staging_connection() as conn:
        row = _fetch_table_row(conn, int(table_id))
        columns = [
            dict(item)
            for item in conn.execute(
                f"SELECT {_COLUMN_COLUMNS_SQL} FROM catalog_column "
                "WHERE table_id = ? ORDER BY ordinal, id",
                (int(table_id),),
            ).fetchall()
        ]
        staging = conn.execute(
            """SELECT staging_name, status, row_count, last_success_at, watermark_value
               FROM staging_table
               WHERE source_id = ? AND namespace = ? AND table_name = ?""",
            (row["source_id"], row["namespace"], row["table_name"]),
        ).fetchone()
    sources = _source_map()
    meta = sources.get(str(row["source_id"]), {})
    detail = dict(row)
    detail["source_name"] = meta.get("name", "")
    detail["columns"] = columns
    detail["column_count"] = len(columns)
    detail["claimed"] = detail.get("owner") != _UNCLAIMED
    detail["staging_available"] = bool(
        staging is not None and str(staging["status"]) == "healthy" and staging["staging_name"]
    )
    detail["staging_status"] = str(staging["status"]) if staging else None
    detail["last_success_at"] = staging["last_success_at"] if staging else None
    return detail


def _require_editor(role: str) -> None:
    if role not in ("admin", "analyst"):
        raise AppError(403, "FORBIDDEN", "目录编辑仅 admin/analyst 可执行")


def _clean_fields(
    fields: dict[str, Any], allowed: dict[str, int], role: str
) -> dict[str, Any]:
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        raise AppError(
            422, "VALIDATION_FAILED", f"不支持修改的字段: {', '.join(unknown)}"
        )
    cleaned: dict[str, Any] = {}
    for key, raw in fields.items():
        if key == "watermark_column":
            if role != "admin":
                raise AppError(403, "FORBIDDEN", "水位列仅 admin 可修改")
            if raw is None:
                cleaned[key] = None
                continue
            text = str(raw).strip()
            cleaned[key] = text or None
            if cleaned[key] and len(cleaned[key]) > allowed[key]:
                raise AppError(422, "VALIDATION_FAILED", "水位列名称不能超过 128 字符")
            continue
        if raw is None:
            if key == "sensitivity_level":
                raise AppError(422, "VALIDATION_FAILED", "敏感级别不能为空")
            cleaned[key] = ""
            continue
        text = str(raw).strip()
        limit = allowed[key]
        if limit and len(text) > limit:
            raise AppError(422, "VALIDATION_FAILED", f"{key} 超过 {limit} 字符")
        if key == "sensitivity_level" and text not in _SENSITIVITY_LEVELS:
            raise AppError(
                422,
                "VALIDATION_FAILED",
                "敏感级别只能是 public/internal/confidential/restricted",
            )
        if key == "owner" and not text:
            text = _UNCLAIMED
        cleaned[key] = text
    return cleaned


def _watermark_exists(conn: sqlite3.Connection, row: sqlite3.Row, column: str) -> bool:
    staging = conn.execute(
        """SELECT columns_json FROM staging_table
           WHERE source_id = ? AND namespace = ? AND table_name = ?""",
        (row["source_id"], row["namespace"], row["table_name"]),
    ).fetchone()
    if staging is None:
        return False
    try:
        columns = json.loads(staging["columns_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        return False
    for item in columns:
        if isinstance(item, dict):
            name = item.get("name")
        elif isinstance(item, (list, tuple)) and item:
            name = item[0]
        else:
            name = None
        if str(name or "") == column:
            return True
    return False


def _log_changes(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int,
    source_id: str,
    table_id: int | None,
    column_id: int | None,
    changes: dict[str, tuple[Any, Any]],
    role: str,
    now: str,
) -> None:
    rows = [
        (
            entity_type,
            entity_id,
            source_id,
            table_id,
            column_id,
            field,
            None if old is None else str(old),
            None if new is None else str(new),
            role,
            now,
        )
        for field, (old, new) in changes.items()
    ]
    conn.executemany(
        """INSERT INTO catalog_change_log
           (entity_type, entity_id, source_id, table_id, column_id, field_name,
            old_value, new_value, changed_by_role, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def update_table(table_id: int, fields: dict[str, Any], role: str) -> dict[str, Any]:
    _require_editor(role)
    store.ensure_staging_available()
    with store.staging_connection() as conn:
        row = _fetch_table_row(conn, int(table_id))
        cleaned = _clean_fields(fields, _TABLE_EDITABLE, role)
        if "watermark_column" in cleaned:
            value = cleaned["watermark_column"]
            current = row["watermark_column"] or None
            if value == current:
                cleaned.pop("watermark_column")
            elif value and not _watermark_exists(conn, row, value):
                raise AppError(
                    422, "VALIDATION_FAILED", "水位列不存在于该表的 staging 结构中"
                )
        changes: dict[str, tuple[Any, Any]] = {}
        for key, value in cleaned.items():
            old = row[key]
            old_text = None if old is None else str(old)
            new_text = None if value is None else str(value)
            if (old_text or "") != (new_text or ""):
                changes[key] = (old, value)
        if not changes:
            return get_table(int(table_id))
        now = _now()
        assignments = [f"{key} = ?" for key in changes]
        params: list[Any] = [value for _, value in changes.values()]
        assignments.extend(["updated_by = ?", "updated_at = ?"])
        params.extend([role, now])
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                f"""UPDATE catalog_table
                    SET {', '.join(assignments)},
                        status = CASE WHEN status = 'pending' THEN 'active' ELSE status END
                    WHERE id = ?""",
                tuple([*params, int(table_id)]),
            )
            if "watermark_column" in changes:
                conn.execute(
                    """UPDATE staging_table SET watermark_value = NULL, updated_at = ?
                       WHERE source_id = ? AND namespace = ? AND table_name = ?""",
                    (now, row["source_id"], row["namespace"], row["table_name"]),
                )
            _log_changes(
                conn,
                entity_type="table",
                entity_id=int(table_id),
                source_id=str(row["source_id"]),
                table_id=int(table_id),
                column_id=None,
                changes=changes,
                role=role,
                now=now,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return get_table(int(table_id))


def _fetch_column_row(conn: sqlite3.Connection, column_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"""SELECT {', '.join('c.' + item.strip() for item in _COLUMN_COLUMNS_SQL.split(','))},
                   t.source_id AS source_id
            FROM catalog_column c
            JOIN catalog_table t ON t.id = c.table_id
            WHERE c.id = ?""",
        (int(column_id),),
    ).fetchone()
    if row is None:
        raise AppError(404, "CATALOG_COLUMN_NOT_FOUND", f"目录字段不存在: {column_id}")
    return row


def _column_dict(conn: sqlite3.Connection, column_id: int) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT {_COLUMN_COLUMNS_SQL} FROM catalog_column WHERE id = ?", (int(column_id),)
    ).fetchone()
    if row is None:
        raise AppError(404, "CATALOG_COLUMN_NOT_FOUND", f"目录字段不存在: {column_id}")
    return dict(row)


def update_column(column_id: int, fields: dict[str, Any], role: str) -> dict[str, Any]:
    _require_editor(role)
    store.ensure_staging_available()
    with store.staging_connection() as conn:
        row = _fetch_column_row(conn, int(column_id))
        cleaned = _clean_fields(fields, _COLUMN_EDITABLE, role)
        changes: dict[str, tuple[Any, Any]] = {}
        for key, value in cleaned.items():
            old = row[key]
            old_text = None if old is None else str(old)
            new_text = None if value is None else str(value)
            if (old_text or "") != (new_text or ""):
                changes[key] = (old, value)
        if not changes:
            return _column_dict(conn, int(column_id))
        now = _now()
        assignments = [f"{key} = ?" for key in changes]
        params: list[Any] = [value for _, value in changes.values()]
        assignments.extend(["updated_by = ?", "updated_at = ?"])
        params.extend([role, now])
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                f"""UPDATE catalog_column
                    SET {', '.join(assignments)},
                        status = CASE WHEN status = 'pending' THEN 'active' ELSE status END
                    WHERE id = ?""",
                tuple([*params, int(column_id)]),
            )
            _log_changes(
                conn,
                entity_type="column",
                entity_id=int(column_id),
                source_id=str(row["source_id"]),
                table_id=int(row["table_id"]),
                column_id=int(column_id),
                changes=changes,
                role=role,
                now=now,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    with store.staging_connection() as conn:
        return _column_dict(conn, int(column_id))


def list_changes(table_id: int, limit: int = 20) -> dict[str, Any]:
    store.ensure_staging_available()
    # API 层 Query(le=100) 已拒绝 >100；此处收敛仅防御直接函数调用。
    try:
        safe_limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        safe_limit = 20
    with store.staging_connection() as conn:
        _fetch_table_row(conn, int(table_id))
        rows = conn.execute(
            """SELECT id, entity_type, entity_id, source_id, table_id, column_id,
                      field_name, old_value, new_value, changed_by_role, created_at
               FROM catalog_change_log WHERE table_id = ?
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (int(table_id), safe_limit),
        ).fetchall()
        total_row = conn.execute(
            "SELECT COUNT(*) AS n FROM catalog_change_log WHERE table_id = ?",
            (int(table_id),),
        ).fetchone()
    return {
        "items": [dict(row) for row in rows],
        "total": int(total_row["n"]) if total_row else 0,
    }


def preview_rows(table_id: int, role: str, limit: int = 5) -> dict[str, Any]:
    store.ensure_staging_available()
    try:
        safe_limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        safe_limit = 5
    with store.staging_connection() as conn:
        table = _fetch_table_row(conn, int(table_id))
        staging = conn.execute(
            """SELECT staging_name, status FROM staging_table
               WHERE source_id = ? AND namespace = ? AND table_name = ?""",
            (table["source_id"], table["namespace"], table["table_name"]),
        ).fetchone()
        if (
            staging is None
            or str(staging["status"]) != "healthy"
            or not staging["staging_name"]
        ):
            raise AppError(
                409, "CATALOG_STAGING_MISSING", "该表尚未同步到 staging 或状态非 healthy"
            )
        try:
            staging_name = writers._ensure_staging_name(str(staging["staging_name"]))
        except Exception as exc:  # noqa: BLE001
            raise AppError(409, "CATALOG_STAGING_MISSING", "staging 表名非法") from exc
        column_rows = conn.execute(
            """SELECT column_name, sensitivity_level FROM catalog_column
               WHERE table_id = ? AND status <> 'missing' ORDER BY ordinal, id""",
            (int(table_id),),
        ).fetchall()
        column_names = [str(row["column_name"]) for row in column_rows]
        if not column_names:
            return {
                "table_id": int(table_id),
                "columns": [],
                "rows": [],
                "masked_columns": [],
                "sensitive_columns": [],
                "limit": safe_limit,
            }
        try:
            selected = ", ".join(quote_ident(name, "sqlite") for name in column_names)
            quoted_staging = quote_ident(staging_name, "sqlite")
        except ConnectorError as exc:
            raise AppError(
                422, "VALIDATION_FAILED", "目录中存在无法安全引用的列名"
            ) from exc
        try:
            raw_rows = [
                dict(row)
                for row in conn.execute(
                    f"SELECT {selected} FROM {quoted_staging} LIMIT ?",
                    (safe_limit,),
                ).fetchall()
            ]
        except sqlite3.Error as exc:
            raise AppError(
                409, "CATALOG_STAGING_MISSING", "staging 表不可读或结构不一致"
            ) from exc
    rows = [
        {name: json_safe(value) for name, value in row.items()} for row in raw_rows
    ]
    sensitive_names = {
        str(row["column_name"]).lower()
        for row in column_rows
        if str(row["sensitivity_level"]) in ("confidential", "restricted")
    }
    sensitive_names.update(
        name.lower() for name in column_names if name.lower() in EXTERNAL_SENSITIVE_COLUMNS
    )
    masked, masked_columns = mask_rows(
        column_names, rows, role, sorted(sensitive_names)
    )
    return {
        "table_id": int(table_id),
        "columns": column_names,
        "rows": masked,
        "masked_columns": masked_columns,
        "sensitive_columns": sorted(sensitive_names),
        "limit": safe_limit,
    }


def summary() -> dict[str, Any]:
    store.ensure_staging_available()
    with store.staging_connection() as conn:
        def scalar(sql: str, params: tuple[Any, ...] = ()) -> int | None:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else None

        tables_total = scalar("SELECT COUNT(*) FROM catalog_table") or 0
        tables_pending = (
            scalar("SELECT COUNT(*) FROM catalog_table WHERE status = 'pending'") or 0
        )
        tables_missing = (
            scalar("SELECT COUNT(*) FROM catalog_table WHERE status = 'missing'") or 0
        )
        tables_unclaimed = (
            scalar("SELECT COUNT(*) FROM catalog_table WHERE owner = ?", (_UNCLAIMED,)) or 0
        )
        columns_total = scalar("SELECT COUNT(*) FROM catalog_column") or 0
        columns_pending = (
            scalar("SELECT COUNT(*) FROM catalog_column WHERE status = 'pending'") or 0
        )
        sources_synced = (
            scalar(
                "SELECT COUNT(DISTINCT source_id) FROM staging_table WHERE status = 'healthy'"
            )
            or 0
        )
        last_sync_row = conn.execute(
            "SELECT MAX(last_success_at) AS value FROM staging_table"
        ).fetchone()
        last_change_row = conn.execute(
            "SELECT MAX(created_at) AS value FROM catalog_change_log"
        ).fetchone()
    sources = _source_map()
    return {
        "tables_total": int(tables_total),
        "tables_pending": int(tables_pending),
        "tables_missing": int(tables_missing),
        "tables_unclaimed": int(tables_unclaimed),
        "columns_total": int(columns_total),
        "columns_pending": int(columns_pending),
        "sources_total": len(sources),
        "sources_synced": int(sources_synced),
        "last_sync_at": last_sync_row["value"] if last_sync_row else None,
        "last_catalog_change_at": last_change_row["value"] if last_change_row else None,
    }
