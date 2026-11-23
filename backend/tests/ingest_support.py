"""同步/目录测试辅助：源构造、同步驱动与 staging 断言。

约定：
- 只使用 ``tmp_path`` 下的业务库、注册库、上传目录与 staging 库；
- 后台线程统一由测试显式替换为 no-op，再由 ``execute_run`` 同步驱动，
  避免 sleep 竞态与 daemon 线程泄漏；
- 断言直接读 staging 表时只选择明确列，不使用 ``SELECT *``。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from fastapi.testclient import TestClient

from app.connectors.base import quote_ident
from app.core.config import settings
from app.db.registry import get_source_row, insert_source_row
from app.services.ingest import catalog, orchestrator, runs, store
from app.services.source_metadata import _snapshot_from_dict
from tests.source_support import csv_bytes, sqlite_bytes, upload_ok, xlsx_bytes
from tests.support import headers, ok_data

CUSTOMER_ROWS: list[list[str]] = [
    ["id", "name", "phone", "email", "amount"],
    ["1", "Alice", "13812345678", "alice@example.com", "10.5"],
    ["2", "Bob", "13700001111", "bob@example.com", "20"],
]

STAGING_TABLE_COLUMNS = (
    "id, source_id, namespace, table_name, staging_name, columns_json, primary_key_json, "
    "watermark_column, watermark_value, sync_mode, row_count, last_run_id, last_success_at, "
    "status, last_error, created_at, updated_at"
)


def upload_csv(
    client: TestClient,
    rows: Iterable[Iterable[Any]],
    *,
    filename: str = "customers.csv",
    name: str = "测试CSV源",
) -> dict[str, Any]:
    """上传 CSV 并返回源对象。"""
    return upload_ok(
        client,
        filename=filename,
        content=csv_bytes(rows),
        kind="csv",
        name=name,
    )


def upload_sqlite(
    client: TestClient, path: Path, *, name: str = "测试SQLite源"
) -> dict[str, Any]:
    """上传 SQLite 文件并返回源对象。"""
    return upload_ok(
        client,
        filename=path.name,
        content=sqlite_bytes(path),
        kind="sqlite",
        name=name,
    )


def upload_excel(
    client: TestClient,
    sheets: dict[str, list[list[Any]]],
    *,
    filename: str = "book.xlsx",
    name: str = "测试Excel源",
) -> dict[str, Any]:
    """在内存生成 xlsx 并上传，返回源对象。"""
    return upload_ok(
        client,
        filename=filename,
        content=xlsx_bytes(sheets),
        kind="excel",
        name=name,
    )


def replace_csv(
    client: TestClient,
    source_id: str,
    rows: Iterable[Iterable[Any]],
    *,
    filename: str = "customers.csv",
) -> dict[str, Any]:
    """替换文件源内容并返回源对象。"""
    from tests.source_support import replace_ok

    return replace_ok(client, source_id, filename=filename, content=csv_bytes(rows))


def register_missing_file_source(
    *, source_id: str = "src_missing01", kind: str = "csv"
) -> dict[str, Any]:
    """直接登记一个指向不存在文件的源，用于失败/重试路径。"""
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    insert_source_row(
        {
            "id": source_id,
            "name": "损坏文件源",
            "kind": kind,
            "config_json": json.dumps({"file_name": "missing.csv"}),
            "secret_ciphertext": None,
            "status": "ready",
            "last_error": None,
            "last_tested_at": None,
            "table_count": 0,
            "metadata_json": None,
            "metadata_updated_at": None,
            "storage_path": str(settings.uploads_dir / source_id / "missing.csv"),
            "original_filename": "missing.csv",
            "size_bytes": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    row = get_source_row(source_id)
    assert row is not None
    return row


def create_database_source(
    client: TestClient,
    *,
    kind: str = "postgresql",
    name: str = "不可达数据库源",
    port: int = 1,
    secret: str = "TASK04_SECRET_MARKER",
) -> dict[str, Any]:
    """登记一个指向本机不可达端口的数据库源（不建立真实连接）。"""
    response = client.post(
        "/api/sources",
        json={
            "name": name,
            "kind": kind,
            "config": {
                "host": "127.0.0.1",
                "port": port,
                "database": "task04",
                "username": "task04",
            },
            "secret": secret,
        },
        headers=headers("admin"),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == "OK", payload
    return payload["data"]


def disable_background_workers(monkeypatch) -> dict[str, Any]:
    """把后台线程替换为记录调用，返回调用记录。"""
    calls: dict[str, Any] = {"runs": [], "batches": []}

    def fake_execute(run_id: str, source_id: str) -> None:
        calls["runs"].append((run_id, source_id))

    def fake_batch(batch: dict[str, Any]) -> None:
        calls["batches"].append(batch)

    monkeypatch.setattr(orchestrator, "_execute_run_safe", fake_execute)
    monkeypatch.setattr(orchestrator, "start_batch_worker", fake_batch)
    return calls


def trigger_sync(client: TestClient, source_id: str, role: str = "admin"):
    """POST 单源同步，返回原始响应。"""
    return client.post(
        f"/api/sources/{source_id}/sync", headers=headers(role)
    )


def execute_run(run_id: str, source_id: str) -> None:
    """同步执行一次编排（测试不依赖 daemon 线程）。"""
    orchestrator._execute_run(run_id, source_id)


def run_sync_now(client: TestClient, source_id: str) -> dict[str, Any]:
    """直接 claim 并同步执行一次单源同步，返回运行详情。

    不经 HTTP 触发与 daemon 线程，保证测试确定性；API 触发路径由
    ``trigger_sync`` + ``disable_background_workers`` 单独覆盖。
    """
    row = get_source_row(source_id)
    assert row is not None
    run = runs.create_run(row, trigger_type="manual", trigger_role="admin")
    execute_run(run["run_id"], source_id)
    return ok_data(
        client.get(f"/api/sync/runs/{run['run_id']}", headers=headers("admin"))
    )


def accepted_data(response) -> dict[str, Any]:
    """断言 HTTP 202 且成功信封 code=OK，返回 data（同步受理响应）。"""
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload.get("code") == "OK", payload
    return payload["data"]


def get_run(client: TestClient, run_id: str) -> dict[str, Any]:
    """读取运行详情。"""
    return ok_data(
        client.get(f"/api/sync/runs/{run_id}", headers=headers("admin"))
    )


def catalog_tables(
    client: TestClient, source_id: str | None = None, **params: Any
) -> list[dict[str, Any]]:
    """读取目录表清单（最多 100 条）。"""
    query: dict[str, Any] = {"page_size": 100, **params}
    if source_id:
        query["source_id"] = source_id
    data = ok_data(
        client.get("/api/catalog/tables", params=query, headers=headers("admin"))
    )
    return data["items"]


def catalog_table_id(client: TestClient, source_id: str, table_name: str) -> int:
    """按源与物理表名查目录表 ID。"""
    for item in catalog_tables(client, source_id):
        if item["table_name"] == table_name:
            return int(item["id"])
    raise AssertionError(f"目录中未找到表: {source_id}/{table_name}")


def catalog_detail(client: TestClient, table_id: int, role: str = "admin") -> dict[str, Any]:
    """读取目录表详情。"""
    return ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}", headers=headers(role)
        )
    )


def catalog_columns(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把详情中的字段列表转成按列名索引的字典。"""
    return {column["column_name"]: column for column in detail["columns"]}


def sync_catalog_from_registry(source_id: str) -> dict[str, Any]:
    """用注册库中的最新元数据快照直接登记目录（绕过 bootstrap 守卫）。"""
    row = get_source_row(source_id)
    assert row is not None and row.get("metadata_json")
    snapshot = _snapshot_from_dict(json.loads(row["metadata_json"]))
    return catalog.sync_source_catalog(source_id, snapshot)


def staging_row(source_id: str, table_name: str) -> dict[str, Any] | None:
    """读取 staging_table 元数据行。"""
    with store.staging_connection() as conn:
        row = conn.execute(
            f"SELECT {STAGING_TABLE_COLUMNS} FROM staging_table "
            "WHERE source_id = ? AND table_name = ?",
            (source_id, table_name),
        ).fetchone()
    return dict(row) if row else None


def staging_row_count(staging_name: str) -> int:
    """物理 staging 表行数。"""
    with store.staging_connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {quote_ident(staging_name, 'sqlite')}"
        ).fetchone()
    return int(row["n"]) if row else 0


def staging_table_names() -> list[str]:
    """staging.db 中的物理 staging 表名（只认 ``stg_*``，不含元数据表）。"""
    with store.staging_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name LIKE 'stg\\_%' ESCAPE '\\'"
        ).fetchall()
    return sorted(str(row["name"]) for row in rows)


def orphan_temp_tables() -> list[str]:
    """staging.db 中遗留的 ``stg_*__new`` 临时表。"""
    with store.staging_connection() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name LIKE 'stg\\_%\\_\\_new' ESCAPE '\\'"
        ).fetchall()
    return sorted(str(row["name"]) for row in rows)


def create_raw_table(name: str, definition: str = "id INTEGER PRIMARY KEY") -> None:
    """在 staging.db 中创建测试用物理表（仅测试辅助）。"""
    with store.staging_connection() as conn:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {quote_ident(name, 'sqlite')} ({definition})"
        )


def raw_table_exists(name: str) -> bool:
    """判断 staging.db 中是否存在指定物理表。"""
    with store.staging_connection() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
    return row is not None


def set_staging_watermark(source_id: str, table_name: str, value: str | None) -> None:
    """直接设置 staging 水位值（模拟历史同步结果）。"""
    with store.staging_connection() as conn:
        conn.execute(
            "UPDATE staging_table SET watermark_value = ? "
            "WHERE source_id = ? AND table_name = ?",
            (value, source_id, table_name),
        )


def set_run_heartbeat(run_id: str, heartbeat_at: str) -> None:
    """直接改写运行心跳时间。"""
    with store.staging_connection() as conn:
        conn.execute(
            "UPDATE ingest_run SET heartbeat_at = ?, updated_at = ? WHERE run_id = ?",
            (heartbeat_at, heartbeat_at, run_id),
        )


def new_run(source_id: str, *, status: str = "succeeded") -> dict[str, Any]:
    """直接创建并终结一条运行记录（用于列表/筛选测试）。"""
    run = runs.create_run(
        {"id": source_id, "name": f"源{source_id}", "kind": "csv"},
        trigger_type="manual",
        trigger_role="admin",
    )
    if status != "queued":
        runs.finish_run(run["run_id"], status)
    return run


def sqlite_file_with_table(path: Path, table: str = "parent", rows: int = 2) -> Path:
    """创建仅含单表的 SQLite 文件（用于 schema diff / 表消失场景）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.executemany(
            f"INSERT INTO {table} (id, name) VALUES (?, ?)",
            [(index, f"row-{index}") for index in range(1, rows + 1)],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def append_sqlite_row(path: Path, table: str, values: tuple[Any, ...]) -> None:
    """向已上传的 SQLite 源文件追加一行（源侧变化模拟）。"""
    conn = sqlite3.connect(str(path))
    try:
        placeholders = ", ".join("?" for _ in values)
        conn.execute(f"INSERT INTO {table} VALUES ({placeholders})", values)
        conn.commit()
    finally:
        conn.close()
