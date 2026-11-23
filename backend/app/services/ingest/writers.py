"""staging 物理表写入：命名、类型映射、行哈希、建表、批量写入与换表。

所有标识符必须来自连接器元数据或本模块生成，并经 ``quote_ident`` 引用；
值一律参数化，禁止把用户输入拼接进 SQL。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from app.connectors.base import ConnectorError, json_safe, quote_ident
from app.services.ingest.store import staging_now

_STAGING_NAME_RE = re.compile(r"^stg_[a-z0-9_]{1,64}$")
_SANITIZE_RE = re.compile(r"[^a-z0-9_]+")
_RESERVED_COLUMNS = {"_row_key", "_ingested_at"}

_INTEGER_TYPES = {
    "INTEGER",
    "INT",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "MEDIUMINT",
    "INT2",
    "INT4",
    "INT8",
    "SERIAL",
    "BIGSERIAL",
    "BOOLEAN",
    "BOOL",
}
_REAL_TYPES = {"REAL", "NUMERIC", "DECIMAL", "DOUBLE", "FLOAT", "FLOAT4", "FLOAT8", "MONEY", "NUMBER"}
_BLOB_TYPES = {"BLOB", "BINARY", "VARBINARY", "BYTEA"}


def _ensure_staging_name(name: str) -> str:
    value = str(name or "")
    if not _STAGING_NAME_RE.match(value):
        raise ConnectorError("非法 staging 表名")
    return value


def staging_name_for(source_id: str, namespace: str, table_name: str) -> str:
    """生成合法 staging 表名；映射由 staging_table.staging_name 持久化。"""
    digest = hashlib.sha1(
        f"{source_id}\x1f{namespace}\x1f{table_name}".encode("utf-8")
    ).hexdigest()[:16]
    sanitized = _SANITIZE_RE.sub("_", str(table_name or "").lower()).strip("_")[:40] or "table"
    name = f"stg_{digest}_{sanitized}"
    if not _STAGING_NAME_RE.match(name):
        raise ConnectorError("无法生成合法 staging 表名")
    return name


def sqlite_affinity(data_type: str) -> str:
    """源类型归一化为 SQLite 亲和类型。"""
    raw = str(data_type or "").strip().upper()
    base = re.split(r"[(\s]", raw, maxsplit=1)[0] if raw else ""
    if base in _INTEGER_TYPES:
        return "INTEGER"
    if base in _REAL_TYPES:
        return "REAL"
    if base in _BLOB_TYPES:
        return "BLOB"
    return "TEXT"


def row_key(columns: list[str], row: dict[str, Any], pk_columns: list[str]) -> str:
    """行唯一键：有主键取主键值数组，无主键取整行值数组，SHA-256。"""
    if pk_columns and all(name in columns for name in pk_columns):
        values = [json_safe(row.get(name)) for name in pk_columns]
    else:
        values = [json_safe(row.get(name)) for name in columns]
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_table(conn: sqlite3.Connection, staging_name: str, columns: list[tuple[str, str]]) -> None:
    """创建 staging 物理表：源列 + _row_key + _ingested_at，唯一约束在行键。"""
    safe_name = _ensure_staging_name(staging_name)
    seen: set[str] = set()
    definitions: list[str] = []
    for name, data_type in columns:
        key = str(name or "").strip()
        if not key:
            raise ConnectorError("源列名为空，无法创建 staging 表")
        lowered = key.lower()
        if lowered in _RESERVED_COLUMNS:
            raise ConnectorError(f"源列名与 staging 保留列冲突: {key}")
        if lowered in seen:
            raise ConnectorError(f"源列名重复: {key}")
        seen.add(lowered)
        definitions.append(f"{quote_ident(key, 'sqlite')} {sqlite_affinity(data_type)}")
    definitions.append("_row_key TEXT NOT NULL")
    definitions.append("_ingested_at TEXT NOT NULL")
    definitions.append("UNIQUE(_row_key)")
    sql = (
        f"CREATE TABLE IF NOT EXISTS {quote_ident(safe_name, 'sqlite')} "
        f"({', '.join(definitions)})"
    )
    conn.execute(sql)


def insert_rows(
    conn: sqlite3.Connection,
    staging_name: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    pk_columns: list[str],
) -> tuple[int, int]:
    """批量 INSERT OR REPLACE；返回（本批写入唯一行数，UTF-8 字节估算）。"""
    if not rows:
        return 0, 0
    safe_name = _ensure_staging_name(staging_name)
    names = [str(name) for name in columns]
    for name in names:
        quote_ident(name, "sqlite")
    select_columns = [quote_ident(name, "sqlite") for name in names]
    select_columns.extend([quote_ident("_row_key", "sqlite"), quote_ident("_ingested_at", "sqlite")])
    placeholders = ", ".join("?" for _ in select_columns)
    sql = (
        f"INSERT OR REPLACE INTO {quote_ident(safe_name, 'sqlite')} "
        f"({', '.join(select_columns)}) VALUES ({placeholders})"
    )

    now = staging_now()
    keys: set[str] = set()
    parameters: list[tuple[Any, ...]] = []
    bytes_estimate = 0
    for row in rows:
        safe_row = {name: json_safe(row.get(name)) for name in names}
        key = row_key(names, safe_row, pk_columns)
        keys.add(key)
        parameters.append(tuple(safe_row[name] for name in names) + (key, now))
        bytes_estimate += len(
            json.dumps(safe_row, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        )
    conn.executemany(sql, parameters)
    return len(keys), bytes_estimate


def table_row_count(conn: sqlite3.Connection, staging_name: str) -> int:
    safe_name = _ensure_staging_name(staging_name)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {quote_ident(safe_name, 'sqlite')}"
    ).fetchone()
    return int(row["n"]) if row else 0


def table_max_value(conn: sqlite3.Connection, staging_name: str, column: str) -> str | None:
    """读取 staging 表指定列最大值（水位推进用）。"""
    safe_name = _ensure_staging_name(staging_name)
    quoted = quote_ident(column, "sqlite")
    row = conn.execute(
        f"SELECT MAX({quoted}) AS value FROM {quote_ident(safe_name, 'sqlite')}"
    ).fetchone()
    if row is None or row["value"] is None:
        return None
    return str(json_safe(row["value"]))


def swap_table(
    conn: sqlite3.Connection, staging_name: str, *, manage_transaction: bool = True
) -> None:
    """原子换表：临时表改名为正式表。

    ``manage_transaction=True`` 时自行 ``BEGIN IMMEDIATE`` / ``COMMIT``；
    调用方需要把换表与元数据更新纳入同一事务时传 ``False``，由外层负责。
    """
    safe_name = _ensure_staging_name(staging_name)
    temp_name = _ensure_staging_name(f"{safe_name}__new")
    quoted_target = quote_ident(safe_name, "sqlite")
    quoted_temp = quote_ident(temp_name, "sqlite")
    if manage_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"DROP TABLE IF EXISTS {quoted_target}")
        conn.execute(f"ALTER TABLE {quoted_temp} RENAME TO {quoted_target}")
        if manage_transaction:
            conn.execute("COMMIT")
    except Exception:
        if manage_transaction:
            conn.execute("ROLLBACK")
        raise


def drop_table_if_exists(conn: sqlite3.Connection, staging_name: str) -> None:
    safe_name = _ensure_staging_name(staging_name)
    conn.execute(f"DROP TABLE IF EXISTS {quote_ident(safe_name, 'sqlite')}")


def drop_orphan_temp_tables(conn: sqlite3.Connection) -> int:
    """清理上次中断遗留的 ``stg_*__new`` 临时表；LIKE 模式为常量。"""
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name LIKE 'stg\\_%\\_\\_new' ESCAPE '\\'"
    ).fetchall()
    removed = 0
    for row in rows:
        name = str(row["name"])
        if not name.endswith("__new") or not _STAGING_NAME_RE.match(name):
            continue
        conn.execute(f"DROP TABLE IF EXISTS {quote_ident(name, 'sqlite')}")
        removed += 1
    return removed
