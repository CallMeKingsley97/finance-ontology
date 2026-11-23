"""事务与失败回滚：catalog 变更日志与 _sync_table 发布段的原子性（CR-101）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.connectors.base import quote_ident
from app.db.registry import get_source_row
from app.services.ingest import catalog, orchestrator, runs, store
from app.services.sources import _connector_for_row
from tests.ingest_support import (
    CUSTOMER_ROWS,
    append_sqlite_row,
    catalog_columns,
    catalog_detail,
    catalog_table_id,
    orphan_temp_tables,
    run_sync_now,
    set_staging_watermark,
    staging_row,
    staging_row_count,
    upload_csv,
    upload_sqlite,
)
from tests.source_support import make_sqlite_db
from tests.support import headers, ok_data


def _fail_log_changes(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("change log failed")


def _fail_metadata_upsert(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("metadata upsert failed")


def _staging_values(staging_name: str, order_column: str) -> list[Any]:
    with store.staging_connection() as conn:
        rows = conn.execute(
            f"SELECT {quote_ident(order_column, 'sqlite')} AS value "
            f"FROM {quote_ident(staging_name, 'sqlite')} "
            f"ORDER BY {quote_ident(order_column, 'sqlite')}"
        ).fetchall()
    return [row["value"] for row in rows]


def test_update_table_rolls_back_when_change_log_fails(client, monkeypatch) -> None:
    source = upload_csv(client, CUSTOMER_ROWS)
    run_sync_now(client, source["id"])
    table_id = catalog_table_id(client, source["id"], "customers")
    set_staging_watermark(source["id"], "customers", "old-wm")

    before = catalog_detail(client, table_id)
    before_changes = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/changes", headers=headers("admin")
        )
    )["total"]

    monkeypatch.setattr(catalog, "_log_changes", _fail_log_changes)
    with pytest.raises(RuntimeError, match="change log failed"):
        catalog.update_table(
            table_id,
            {
                "display_name": "新客户名称",
                "owner": "财务共享中心",
                "watermark_column": "id",
            },
            "admin",
        )

    after = catalog.get_table(table_id)
    assert after["display_name"] == before["display_name"]
    assert after["owner"] == before["owner"]
    assert after["watermark_column"] == before["watermark_column"]
    assert after["status"] == before["status"]
    assert after["updated_by"] == before["updated_by"]

    staging = staging_row(source["id"], "customers")
    assert staging is not None
    assert staging["watermark_value"] == "old-wm"

    after_changes = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/changes", headers=headers("admin")
        )
    )["total"]
    assert after_changes == before_changes


def test_update_column_rolls_back_when_change_log_fails(client, monkeypatch) -> None:
    source = upload_csv(client, CUSTOMER_ROWS)
    run_sync_now(client, source["id"])
    table_id = catalog_table_id(client, source["id"], "customers")
    detail = catalog_detail(client, table_id)
    column_id = int(catalog_columns(detail)["name"]["id"])

    before = catalog_detail(client, table_id)
    before_column = catalog_columns(before)["name"]

    monkeypatch.setattr(catalog, "_log_changes", _fail_log_changes)
    with pytest.raises(RuntimeError, match="change log failed"):
        catalog.update_column(
            column_id,
            {"display_name": "姓名", "semantic_type": "text", "sensitivity_level": "public"},
            "analyst",
        )

    after = catalog.get_table(table_id)
    after_column = catalog_columns(after)["name"]
    assert after_column["display_name"] == before_column["display_name"]
    assert after_column["semantic_type"] == before_column["semantic_type"]
    assert after_column["sensitivity_level"] == before_column["sensitivity_level"]
    assert after_column["status"] == before_column["status"]
    assert after_column["updated_by"] == before_column["updated_by"]

    changes = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/changes", headers=headers("admin")
        )
    )["total"]
    assert changes == 0


def test_full_publish_failure_keeps_previous_table_and_metadata(
    client, monkeypatch, tmp_path: Path
) -> None:
    source_path = make_sqlite_db(tmp_path / "source.db")
    source = upload_sqlite(client, source_path)
    run = run_sync_now(client, source["id"])
    assert run["status"] == "succeeded"

    before = staging_row(source["id"], "parent")
    assert before is not None
    assert before["row_count"] == 2
    staging_name = str(before["staging_name"])
    assert staging_row_count(staging_name) == 2

    row = get_source_row(source["id"])
    assert row is not None
    append_sqlite_row(Path(row["storage_path"]), "parent", (3, "gamma"))

    connector = _connector_for_row(row)
    try:
        snapshot = connector.inspect()
        table = next(item for item in snapshot.tables if item.name == "parent")
        monkeypatch.setattr(orchestrator, "_upsert_staging_table", _fail_metadata_upsert)
        with pytest.raises(RuntimeError, match="metadata upsert failed"):
            orchestrator._sync_table(
                connector, table, row, "run_publish_fail", []
            )
    finally:
        connector.close()

    after = staging_row(source["id"], "parent")
    assert after is not None
    assert after["row_count"] == before["row_count"] == 2
    assert after["last_run_id"] == before["last_run_id"]
    assert after["last_success_at"] == before["last_success_at"]
    assert after["status"] == "healthy"
    assert staging_row_count(staging_name) == 2
    assert _staging_values(staging_name, "id") == [1, 2]

    # 换表事务回滚后临时表成为孤儿；无活跃运行时由恢复流程清理。
    assert orphan_temp_tables(), "预期存在回滚后的 __new 临时表"
    recovery = runs.recover_interrupted_runs()
    assert recovery["orphans_dropped"] >= 1
    assert orphan_temp_tables() == []


def test_incremental_sync_advances_watermark(client, tmp_path: Path) -> None:
    source_path = make_sqlite_db(tmp_path / "source.db")
    source = upload_sqlite(client, source_path)
    run_sync_now(client, source["id"])

    table_id = catalog_table_id(client, source["id"], "parent")
    catalog.update_table(table_id, {"watermark_column": "id"}, "admin")
    set_staging_watermark(source["id"], "parent", "1")

    row = get_source_row(source["id"])
    assert row is not None
    append_sqlite_row(Path(row["storage_path"]), "parent", (3, "gamma"))

    connector = _connector_for_row(row)
    try:
        snapshot = connector.inspect()
        table = next(item for item in snapshot.tables if item.name == "parent")
        result = orchestrator._sync_table(
            connector, table, {**row, "kind": "postgresql"}, "run_incremental_ok", []
        )
    finally:
        connector.close()

    assert result["mode"] == "incremental"
    staging = staging_row(source["id"], "parent")
    assert staging is not None
    assert staging["row_count"] == 3
    assert staging["watermark_value"] == "3"
    assert staging["sync_mode"] == "incremental"
    assert staging_row_count(staging["staging_name"]) == 3
    assert _staging_values(staging["staging_name"], "id") == [1, 2, 3]


def test_incremental_publish_failure_keeps_metadata_and_watermark(
    client, monkeypatch, tmp_path: Path
) -> None:
    source_path = make_sqlite_db(tmp_path / "source.db")
    source = upload_sqlite(client, source_path)
    run_sync_now(client, source["id"])

    table_id = catalog_table_id(client, source["id"], "parent")
    catalog.update_table(table_id, {"watermark_column": "id"}, "admin")
    set_staging_watermark(source["id"], "parent", "1")

    before = staging_row(source["id"], "parent")
    assert before is not None
    assert before["watermark_value"] == "1"
    assert before["row_count"] == 2

    row = get_source_row(source["id"])
    assert row is not None
    append_sqlite_row(Path(row["storage_path"]), "parent", (3, "gamma"))

    connector = _connector_for_row(row)
    try:
        snapshot = connector.inspect()
        table = next(item for item in snapshot.tables if item.name == "parent")
        # kind 改为数据库源以启用增量模式；连接器仍是只读 SQLite 文件。
        database_row = {**row, "kind": "postgresql"}
        monkeypatch.setattr(orchestrator, "_upsert_staging_table", _fail_metadata_upsert)
        with pytest.raises(RuntimeError, match="metadata upsert failed"):
            orchestrator._sync_table(
                connector, table, database_row, "run_incremental_fail", []
            )
    finally:
        connector.close()

    after = staging_row(source["id"], "parent")
    assert after is not None
    # 增量写入按批自动提交，但元数据与水位必须保持旧值，保证下次重读。
    assert after["row_count"] == before["row_count"] == 2
    assert after["watermark_value"] == before["watermark_value"] == "1"
    assert after["last_run_id"] == before["last_run_id"]
    assert after["last_success_at"] == before["last_success_at"]
