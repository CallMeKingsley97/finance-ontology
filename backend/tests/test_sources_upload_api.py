"""文件上传、替换与清理的 API 集成测试（PT-03/PT-04 风险点）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.registry import get_source_row
from app.services import sources as sources_service
from tests.source_support import (
    csv_bytes,
    make_sqlite_db,
    replace_file,
    replace_ok,
    residual_temp_dirs,
    sqlite_bytes,
    upload_artifacts,
    upload_file,
    upload_ok,
    xlsx_bytes,
)
from tests.support import error_data, headers, ok_data


def _table_detail(client, source_id: str, table: str) -> dict:
    return ok_data(
        client.get(
            f"/api/sources/{source_id}/metadata/table",
            params={"table": table, "limit": 5},
        )
    )


def _source_dirs() -> list[Path]:
    return [item for item in upload_artifacts() if item.name.startswith("src_")]


# ---------------------------------------------------------------- upload success


def test_upload_csv_utf8_creates_ready_source(client) -> None:
    source = upload_ok(
        client,
        filename="sales.csv",
        content=csv_bytes(
            [
                ["id", "name", "amount"],
                ["1", "甲", "10.5"],
                ["2", "乙", "20"],
            ]
        ),
        kind="csv",
        name="销售源",
    )
    assert source["kind"] == "csv"
    assert source["status"] == "ready"
    assert source["managed"] is True
    assert source["table_count"] == 1
    assert source["path"] == ""
    assert source["original_filename"] == "sales.csv"
    assert source["size_bytes"] > 0
    assert source["config"]["delimiter"] == ","
    assert source["config"]["header"] is True
    assert source["config"]["encoding"].startswith("utf-8")

    metadata = ok_data(client.get(f"/api/sources/{source['id']}/metadata"))
    assert metadata["kind"] == "csv"
    assert metadata["cached"] is True
    assert [table["name"] for table in metadata["tables"]] == ["sales"]

    detail = _table_detail(client, source["id"], "sales")
    assert detail["source_id"] == source["id"]
    assert detail["schema"] == "file"
    assert detail["table_type"] == "file"
    assert detail["row_count"] == 2
    assert [column["name"] for column in detail["columns"]] == [
        "id",
        "name",
        "amount",
    ]
    assert [column["type"] for column in detail["columns"]] == [
        "INTEGER",
        "TEXT",
        "REAL",
    ]
    assert len(detail["sample_rows"]) == 2
    assert detail["sample_rows"][0]["name"] == "甲"


def test_upload_gb18030_semicolon_csv(client) -> None:
    source = upload_ok(
        client,
        filename="gb_data.csv",
        content="编号;姓名;金额\n1;张三;100.5\n2;李四;200\n".encode("gb18030"),
        kind="csv",
        name="GB18030 源",
    )
    assert source["config"]["delimiter"] == ";"
    assert source["config"]["header"] is True

    detail = _table_detail(client, source["id"], "gb_data")
    assert [column["name"] for column in detail["columns"]] == ["编号", "姓名", "金额"]
    assert [column["type"] for column in detail["columns"]] == [
        "INTEGER",
        "TEXT",
        "REAL",
    ]
    assert detail["sample_rows"][0]["姓名"] == "张三"


def test_upload_csv_without_header_and_duplicate_headers(client) -> None:
    no_header = upload_ok(
        client,
        filename="matrix.csv",
        content=b"1,2\n3,4\n",
        kind="csv",
    )
    no_header_detail = _table_detail(client, no_header["id"], "matrix")
    assert [column["name"] for column in no_header_detail["columns"]] == [
        "column_1",
        "column_2",
    ]
    assert no_header_detail["row_count"] == 2
    assert no_header["config"]["header"] is False

    duplicated = upload_ok(
        client,
        filename="duplicated.csv",
        content=b"name,name,extra,id\nAlice,Bob,x,1\n",
        kind="csv",
    )
    duplicated_detail = _table_detail(client, duplicated["id"], "duplicated")
    # 重复表头不满足表头判定规则，CSV 连接器按无表头数据处理。
    assert duplicated["config"]["header"] is False
    assert [column["name"] for column in duplicated_detail["columns"]] == [
        "column_1",
        "column_2",
        "column_3",
        "column_4",
    ]


def test_upload_excel_multi_sheet_and_reject_xls(client) -> None:
    source = upload_ok(
        client,
        filename="book.xlsx",
        content=xlsx_bytes(
            {
                "销售数据": [
                    ["id", "name", "amount"],
                    [1, "张三", 10.5],
                    [2, "李四", 20.25],
                ],
                "空表": [],
            }
        ),
        kind="excel",
        name="Excel 源",
    )
    assert source["kind"] == "excel"
    assert source["table_count"] == 1
    assert source["config"]["sheet_count"] == 1

    detail = _table_detail(client, source["id"], "销售数据")
    assert detail["schema"] == "excel"
    assert [column["name"] for column in detail["columns"]] == ["id", "name", "amount"]
    assert [column["type"] for column in detail["columns"]] == [
        "INTEGER",
        "TEXT",
        "REAL",
    ]
    assert detail["row_count"] == 2

    before = len(_source_dirs())
    legacy = upload_file(
        client,
        filename="legacy.xls",
        content=b"legacy-excel-binary",
        kind="excel",
    )
    assert legacy.status_code == 422
    assert error_data(legacy)["code"] == "UPLOAD_INVALID_TYPE"
    assert len(_source_dirs()) == before


def test_upload_sqlite_source_test_and_delete_cleanup(client, tmp_path: Path) -> None:
    db_file = make_sqlite_db(tmp_path / "warehouse.db")
    source = upload_ok(
        client,
        filename="warehouse.db",
        content=sqlite_bytes(db_file),
        kind="sqlite",
        name="仓库库",
    )
    assert source["kind"] == "sqlite"
    assert source["table_count"] == 2
    assert source["original_filename"] == "warehouse.db"

    metadata = ok_data(client.get(f"/api/sources/{source['id']}/metadata"))
    assert {table["name"] for table in metadata["tables"]} == {"parent", "child"}

    child = _table_detail(client, source["id"], "child")
    assert child["primary_key"] == ["id"]
    assert child["foreign_keys"][0]["to_table"] == "parent"
    assert any(
        index["name"] == "idx_child_parent" and index["unique"]
        for index in child["indexes"]
    )

    tested = ok_data(
        client.post(f"/api/sources/{source['id']}/test", headers=headers("admin"))
    )
    assert tested["success"] is True
    assert tested["table_count"] == 2
    assert tested["sqlite_version"]
    assert tested["server_version"]

    source_dir = settings.uploads_dir / source["id"]
    assert source_dir.exists()
    deleted = ok_data(
        client.delete(f"/api/sources/{source['id']}", headers=headers("admin"))
    )
    assert deleted == {"deleted": True, "id": source["id"]}
    assert client.get(f"/api/sources/{source['id']}").status_code == 404
    assert not source_dir.exists()
    assert residual_temp_dirs() == []


# ---------------------------------------------------------------- upload failures


def test_upload_too_large_returns_413_and_cleans_directory(client) -> None:
    settings.source_max_csv_bytes = 32
    response = upload_file(
        client,
        filename="too_big.csv",
        content=b"a,b\n" + b"x" * 64,
        kind="csv",
    )
    assert response.status_code == 413
    assert error_data(response)["code"] == "UPLOAD_TOO_LARGE"
    assert _source_dirs() == []


def test_upload_rejects_magic_empty_and_bad_kind(client) -> None:
    bad_excel = upload_file(
        client, filename="book.xlsx", content=b"not-a-zip", kind="excel"
    )
    assert bad_excel.status_code == 422
    assert error_data(bad_excel)["code"] == "UPLOAD_INVALID_TYPE"

    bad_sqlite = upload_file(
        client, filename="data.db", content=b"not-a-sqlite", kind="sqlite"
    )
    assert bad_sqlite.status_code == 422
    assert error_data(bad_sqlite)["code"] == "UPLOAD_INVALID_TYPE"

    binary_csv = upload_file(
        client, filename="binary.csv", content=b"a,b\n\x00\x01", kind="csv"
    )
    assert binary_csv.status_code == 422
    assert error_data(binary_csv)["code"] == "UPLOAD_INVALID_TYPE"

    empty = upload_file(client, filename="empty.csv", content=b"", kind="csv")
    assert empty.status_code == 422
    assert error_data(empty)["code"] == "UPLOAD_PARSE_FAILED"

    wrong_extension = upload_file(
        client, filename="data.txt", content=b"a,b\n1,2\n", kind="csv"
    )
    assert wrong_extension.status_code == 422
    assert error_data(wrong_extension)["code"] == "UPLOAD_INVALID_TYPE"

    unsupported = upload_file(
        client, filename="data.csv", content=b"a,b\n1,2\n", kind="parquet"
    )
    assert unsupported.status_code == 422
    assert error_data(unsupported)["code"] == "SOURCE_KIND_UNSUPPORTED"

    assert _source_dirs() == []


# ---------------------------------------------------------------- replace


def test_replace_file_success_preserves_identity_and_updates_metadata(client) -> None:
    source = upload_ok(
        client,
        filename="customers.csv",
        content=csv_bytes([["id", "name"], ["1", "张三"]]),
        kind="csv",
        name="客户表",
    )
    old_row = get_source_row(source["id"])
    assert old_row is not None
    old_file = Path(old_row["storage_path"])
    assert old_file.exists()
    old_bytes = old_file.read_bytes()

    replaced = replace_ok(
        client,
        source["id"],
        filename="customers.csv",
        content=csv_bytes([["id", "name"], ["2", "王五"]]),
    )
    assert replaced["id"] == source["id"]
    assert replaced["name"] == "客户表"
    assert replaced["kind"] == "csv"
    assert replaced["original_filename"] == "customers.csv"
    assert replaced["size_bytes"] > 0

    new_row = get_source_row(source["id"])
    assert new_row is not None
    new_file = Path(new_row["storage_path"])
    assert new_file.exists()
    assert new_file != old_file
    assert not old_file.exists()
    assert new_file.read_bytes() != old_bytes
    assert residual_temp_dirs() == []

    detail = _table_detail(client, source["id"], "customers")
    assert detail["sample_rows"][0]["name"] == "王五"


def test_replace_failure_rolls_back_file_and_registry(lenient_client, monkeypatch) -> None:
    client = lenient_client
    source = upload_ok(
        client,
        filename="customers.csv",
        content=csv_bytes([["id", "name"], ["1", "张三"]]),
        kind="csv",
        name="客户表",
    )
    old_row = get_source_row(source["id"])
    assert old_row is not None
    old_file = Path(old_row["storage_path"])
    old_bytes = old_file.read_bytes()
    compared_fields = (
        "storage_path",
        "metadata_json",
        "table_count",
        "config_json",
        "size_bytes",
        "original_filename",
        "status",
        "updated_at",
    )
    old_values = {field: old_row[field] for field in compared_fields}

    def fail_update(*args, **kwargs):
        raise RuntimeError("injected registry failure")

    monkeypatch.setattr(sources_service, "update_source_row", fail_update)
    failed = replace_file(
        client,
        source["id"],
        filename="customers.csv",
        content=csv_bytes([["id", "name"], ["2", "王五"]]),
    )
    assert failed.status_code == 500
    assert error_data(failed)["code"] == "INTERNAL_ERROR"

    after_row = get_source_row(source["id"])
    assert after_row is not None
    assert {field: after_row[field] for field in compared_fields} == old_values
    assert old_file.exists()
    assert old_file.read_bytes() == old_bytes
    assert [item.name for item in (settings.uploads_dir / source["id"]).iterdir()] == [
        old_file.name
    ]
    assert residual_temp_dirs() == []

    detail = _table_detail(client, source["id"], "customers")
    assert detail["sample_rows"][0]["name"] == "张三"
    assert ok_data(client.get(f"/api/sources/{source['id']}"))["status"] == "ready"

    # 恢复后再次替换应成功，且不遗留备份/临时目录。
    monkeypatch.undo()
    recovered = replace_ok(
        client,
        source["id"],
        filename="customers.csv",
        content=csv_bytes([["id", "name"], ["3", "赵六"]]),
    )
    assert recovered["id"] == source["id"]
    assert residual_temp_dirs() == []
    assert _table_detail(client, source["id"], "customers")["sample_rows"][0][
        "name"
    ] == "赵六"


def test_replace_parse_failure_keeps_old_excel_file(client) -> None:
    source = upload_ok(
        client,
        filename="book.xlsx",
        content=xlsx_bytes(
            {"数据": [["id", "name"], [1, "张三"]]}
        ),
        kind="excel",
        name="Excel 源",
    )
    old_row = get_source_row(source["id"])
    assert old_row is not None
    old_file = Path(old_row["storage_path"])
    old_bytes = old_file.read_bytes()

    failed = replace_file(
        client,
        source["id"],
        filename="book.xlsx",
        content=b"PK\x03\x04this-is-not-a-valid-xlsx",
    )
    assert failed.status_code == 422
    assert error_data(failed)["code"] == "UPLOAD_PARSE_FAILED"

    after_row = get_source_row(source["id"])
    assert after_row is not None
    assert after_row["storage_path"] == old_row["storage_path"]
    assert after_row["metadata_json"] == old_row["metadata_json"]
    assert old_file.exists()
    assert old_file.read_bytes() == old_bytes
    assert residual_temp_dirs() == []
    assert _table_detail(client, source["id"], "数据")["sample_rows"][0]["name"] == "张三"


def test_replace_rejects_database_and_builtin_sources(client) -> None:
    database = ok_data(
        client.post(
            "/api/sources",
            json={
                "name": "PG 源",
                "kind": "postgresql",
                "config": {
                    "host": "pg.internal",
                    "port": 5432,
                    "database": "finance",
                    "username": "readonly",
                },
                "secret": "pg-secret",
            },
            headers=headers("admin"),
        )
    )
    not_file = replace_file(
        client,
        database["id"],
        filename="data.csv",
        content=csv_bytes([["id"], ["1"]]),
    )
    assert not_file.status_code == 409
    assert error_data(not_file)["code"] == "SOURCE_NOT_FILE"

    builtin = replace_file(
        client,
        "sqlite-local",
        filename="data.csv",
        content=csv_bytes([["id"], ["1"]]),
    )
    assert builtin.status_code == 409
    assert error_data(builtin)["code"] == "BUILTIN_SOURCE"
