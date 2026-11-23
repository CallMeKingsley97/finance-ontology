"""独立数据源注册库（data/sources.db）。

与业务库 finance.db 物理隔离，`seed_database(reset=True)` 不会影响注册信息。
所有 SQL 使用参数化查询；动态列名仅来自固定白名单。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app.core.config import settings

_SOURCE_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "kind",
    "config_json",
    "secret_ciphertext",
    "status",
    "last_error",
    "last_tested_at",
    "table_count",
    "metadata_json",
    "metadata_updated_at",
    "storage_path",
    "original_filename",
    "size_bytes",
    "created_at",
    "updated_at",
)

# 允许 update 的列：id 主键与 created_at 不可变。
_UPDATE_COLUMNS = frozenset(_SOURCE_COLUMNS) - {"id", "created_at"}

_DDL = """
CREATE TABLE IF NOT EXISTS data_source (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('sqlite','postgresql','mysql','csv','excel')),
    config_json         TEXT NOT NULL DEFAULT '{}',
    secret_ciphertext   TEXT,
    status              TEXT NOT NULL DEFAULT 'new'
                        CHECK (status IN ('new','ready','failed')),
    last_error          TEXT,
    last_tested_at      TEXT,
    table_count         INTEGER NOT NULL DEFAULT 0,
    metadata_json       TEXT,
    metadata_updated_at TEXT,
    storage_path        TEXT,
    original_filename   TEXT,
    size_bytes          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_data_source_kind ON data_source(kind);
CREATE INDEX IF NOT EXISTS idx_data_source_created ON data_source(created_at);
"""

_initialized: set[str] = set()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _db_path() -> Path:
    return settings.sources_db_path


def init_registry() -> None:
    """首次调用执行 DDL；重复调用为幂等。"""
    path = _db_path()
    key = str(path)
    if key in _initialized and path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA user_version = 1")
        conn.executescript(_DDL)
    finally:
        conn.close()
    _initialized.add(key)


@contextmanager
def registry_connection() -> Iterator[sqlite3.Connection]:
    """注册库连接：自动提交/回滚，busy_timeout=5000。"""
    init_registry()
    conn = sqlite3.connect(str(_db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_source_rows() -> list[dict[str, Any]]:
    with registry_connection() as conn:
        rows = conn.execute(
            """SELECT id, name, kind, config_json, secret_ciphertext, status,
                      last_error, last_tested_at, table_count, metadata_json,
                      metadata_updated_at, storage_path, original_filename,
                      size_bytes, created_at, updated_at
               FROM data_source
               ORDER BY created_at DESC, id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


def get_source_row(source_id: str) -> dict[str, Any] | None:
    with registry_connection() as conn:
        row = conn.execute(
            """SELECT id, name, kind, config_json, secret_ciphertext, status,
                      last_error, last_tested_at, table_count, metadata_json,
                      metadata_updated_at, storage_path, original_filename,
                      size_bytes, created_at, updated_at
               FROM data_source
               WHERE id = ?""",
            (source_id,),
        ).fetchone()
    return dict(row) if row else None


def insert_source_row(row: dict[str, Any]) -> None:
    values = {column: row.get(column) for column in _SOURCE_COLUMNS}
    values["config_json"] = values.get("config_json") or "{}"
    values["status"] = values.get("status") or "new"
    values["table_count"] = int(values.get("table_count") or 0)
    values["size_bytes"] = int(values.get("size_bytes") or 0)
    for column in ("created_at", "updated_at"):
        values[column] = values.get(column) or _now()
    with registry_connection() as conn:
        conn.execute(
            """INSERT INTO data_source
               (id, name, kind, config_json, secret_ciphertext, status, last_error,
                last_tested_at, table_count, metadata_json, metadata_updated_at,
                storage_path, original_filename, size_bytes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(values[column] for column in _SOURCE_COLUMNS),
        )


def update_source_row(source_id: str, **fields: Any) -> None:
    """按白名单更新注册行；未知字段忽略，列名不来自用户输入。"""
    updates = {key: value for key, value in fields.items() if key in _UPDATE_COLUMNS}
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{column} = ?" for column in updates)
    params = [*updates.values(), source_id]
    with registry_connection() as conn:
        conn.execute(f"UPDATE data_source SET {assignments} WHERE id = ?", params)


def delete_source_row(source_id: str) -> bool:
    with registry_connection() as conn:
        cursor = conn.execute("DELETE FROM data_source WHERE id = ?", (source_id,))
    return cursor.rowcount > 0
