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

# ===== 外部 auto-ontology 绑定配置（单行表） =====
AUTO_ONTOLOGY_BINDING_ID = 1

_AUTO_ONTOLOGY_COLUMNS: tuple[str, ...] = (
    "id",
    "base_url",
    "auth_header",
    "token_ciphertext",
    "enabled",
    "verify_tls",
    "probe_path",
    "timeout_seconds",
    "status",
    "last_error",
    "last_tested_at",
    "last_latency_ms",
    "last_status_code",
    "created_at",
    "updated_at",
)

# 插入/冲突更新时写入的列；created_at 只在首次插入时使用。
_AUTO_ONTOLOGY_UPSERT_COLUMNS: tuple[str, ...] = tuple(
    column for column in _AUTO_ONTOLOGY_COLUMNS if column != "created_at"
)

_AUTO_ONTOLOGY_NULLABLE: frozenset[str] = frozenset(
    {"token_ciphertext", "last_error", "last_tested_at", "last_latency_ms", "last_status_code"}
)

_AUTO_ONTOLOGY_DEFAULTS: dict[str, Any] = {
    "id": AUTO_ONTOLOGY_BINDING_ID,
    "base_url": "",
    "auth_header": "Authorization",
    "token_ciphertext": None,
    "enabled": 0,
    "verify_tls": 1,
    "probe_path": "/api/terms?limit=1",
    "timeout_seconds": 5.0,
    "status": "unconfigured",
    "last_error": None,
    "last_tested_at": None,
    "last_latency_ms": None,
    "last_status_code": None,
}

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

CREATE TABLE IF NOT EXISTS auto_ontology_binding (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    base_url            TEXT NOT NULL DEFAULT '',
    auth_header         TEXT NOT NULL DEFAULT 'Authorization',
    token_ciphertext    TEXT,
    enabled             INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    verify_tls          INTEGER NOT NULL DEFAULT 1 CHECK (verify_tls IN (0, 1)),
    probe_path          TEXT NOT NULL DEFAULT '/api/terms?limit=1',
    timeout_seconds     REAL NOT NULL DEFAULT 5.0
                        CHECK (timeout_seconds > 0 AND timeout_seconds <= 10),
    status              TEXT NOT NULL DEFAULT 'unconfigured'
                        CHECK (status IN ('unconfigured','disabled','healthy','error')),
    last_error          TEXT,
    last_tested_at      TEXT,
    last_latency_ms     INTEGER,
    last_status_code    INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
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


# ===== auto-ontology 绑定配置访问 =====


def get_auto_ontology_binding() -> dict[str, Any] | None:
    """读取单行绑定配置；无行返回 None。列名固定，不使用 SELECT *。"""
    with registry_connection() as conn:
        row = conn.execute(
            """SELECT id, base_url, auth_header, token_ciphertext, enabled,
                      verify_tls, probe_path, timeout_seconds, status,
                      last_error, last_tested_at, last_latency_ms, last_status_code,
                      created_at, updated_at
               FROM auto_ontology_binding
               WHERE id = ?""",
            (AUTO_ONTOLOGY_BINDING_ID,),
        ).fetchone()
    return dict(row) if row else None


def upsert_auto_ontology_binding(fields: dict[str, Any]) -> None:
    """固定列 UPSERT；所有值走 ? 参数，缺失字段使用默认值补齐。"""
    values = dict(_AUTO_ONTOLOGY_DEFAULTS)
    for column in _AUTO_ONTOLOGY_UPSERT_COLUMNS:
        if column not in fields:
            continue
        value = fields[column]
        if value is None and column not in _AUTO_ONTOLOGY_NULLABLE:
            continue
        values[column] = value
    values["id"] = AUTO_ONTOLOGY_BINDING_ID
    values["created_at"] = fields.get("created_at") or _now()
    values["updated_at"] = fields.get("updated_at") or _now()

    with registry_connection() as conn:
        conn.execute(
            """INSERT INTO auto_ontology_binding
               (id, base_url, auth_header, token_ciphertext, enabled, verify_tls,
                probe_path, timeout_seconds, status, last_error, last_tested_at,
                last_latency_ms, last_status_code, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   base_url = excluded.base_url,
                   auth_header = excluded.auth_header,
                   token_ciphertext = excluded.token_ciphertext,
                   enabled = excluded.enabled,
                   verify_tls = excluded.verify_tls,
                   probe_path = excluded.probe_path,
                   timeout_seconds = excluded.timeout_seconds,
                   status = excluded.status,
                   last_error = excluded.last_error,
                   last_tested_at = excluded.last_tested_at,
                   last_latency_ms = excluded.last_latency_ms,
                   last_status_code = excluded.last_status_code,
                   updated_at = excluded.updated_at""",
            tuple(values[column] for column in _AUTO_ONTOLOGY_COLUMNS),
        )


def update_auto_ontology_binding_test_result(
    *,
    status: str,
    last_error: str | None,
    last_tested_at: str,
    last_latency_ms: int | None,
    last_status_code: int | None,
) -> None:
    """只更新测试结果列，不修改配置字段。"""
    with registry_connection() as conn:
        conn.execute(
            """UPDATE auto_ontology_binding
               SET status = ?, last_error = ?, last_tested_at = ?,
                   last_latency_ms = ?, last_status_code = ?, updated_at = ?
               WHERE id = ?""",
            (
                status,
                last_error,
                last_tested_at,
                last_latency_ms,
                last_status_code,
                _now(),
                AUTO_ONTOLOGY_BINDING_ID,
            ),
        )


def delete_auto_ontology_binding() -> bool:
    """删除单行绑定配置，返回是否实际删除。"""
    with registry_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM auto_ontology_binding WHERE id = ?",
            (AUTO_ONTOLOGY_BINDING_ID,),
        )
    return cursor.rowcount > 0
