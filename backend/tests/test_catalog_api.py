"""元数据目录 API：bootstrap、schema diff、人工字段保护、留痕与脱敏预览。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.ingest import catalog, store
from tests.ingest_support import (
    CUSTOMER_ROWS,
    catalog_columns,
    catalog_detail,
    catalog_table_id,
    catalog_tables,
    run_sync_now,
    staging_row,
    set_staging_watermark,
    sync_catalog_from_registry,
    upload_csv,
    upload_sqlite,
)
from tests.source_support import make_sqlite_db, replace_ok, sqlite_bytes
from tests.support import headers, ok_data


def _catalog_list(client, **params) -> dict:
    return ok_data(
        client.get("/api/catalog/tables", params=params, headers=headers("admin"))
    )


def _upload_synced_customers(client) -> dict:
    source = upload_csv(client, CUSTOMER_ROWS)
    run_sync_now(client, source["id"])
    return source


def test_builtin_catalog_bootstrap_is_idempotent(client) -> None:
    summary = ok_data(
        client.get("/api/catalog/summary", headers=headers("admin"))
    )
    assert summary["tables_total"] == 10
    assert summary["tables_pending"] == 10
    assert summary["tables_unclaimed"] == 10
    assert summary["sources_total"] == 0
    assert summary["columns_total"] >= 80

    items = catalog_tables(client, "sqlite-local")
    assert len(items) == 10
    assert len({item["table_name"] for item in items}) == 10
    assert all(item["owner"] == "待认领" for item in items)
    assert all(item["status"] == "pending" for item in items)
    assert all(item["source_kind"] == "sqlite" for item in items)
    assert sum(item["column_count"] for item in items) == summary["columns_total"]

    catalog.ensure_bootstrap()
    catalog.ensure_bootstrap()
    assert catalog.summary()["tables_total"] == 10
    with store.staging_connection() as conn:
        table_count = conn.execute(
            "SELECT COUNT(*) AS n FROM catalog_table"
        ).fetchone()["n"]
        change_count = conn.execute(
            "SELECT COUNT(*) AS n FROM catalog_change_log"
        ).fetchone()["n"]
    assert int(table_count) == 10
    assert int(change_count) == 0


def test_external_source_registration_and_filters(client) -> None:
    source = upload_csv(client, CUSTOMER_ROWS)
    items = catalog_tables(client, source["id"])
    assert len(items) == 1
    item = items[0]
    assert item["table_name"] == "customers"
    assert item["status"] == "pending"
    assert item["owner"] == "待认领"
    assert item["claimed"] is False
    assert item["source_kind"] == "csv"
    assert item["column_count"] == 5
    assert item["row_count"] == 2

    detail = catalog_detail(client, int(item["id"]))
    columns = catalog_columns(detail)
    assert list(columns) == ["id", "name", "phone", "email", "amount"]
    assert columns["phone"]["sensitivity_level"] == "confidential"
    assert columns["email"]["sensitivity_level"] == "confidential"
    assert columns["id"]["sensitivity_level"] == "internal"
    assert columns["id"]["is_primary_key"] == 0

    searched = _catalog_list(client, source_id=source["id"], q="CUSTOM")
    assert searched["total"] == 1
    pending = _catalog_list(client, source_id=source["id"], status="pending")
    assert pending["total"] == 1
    unclaimed = _catalog_list(client, source_id=source["id"], owner="待认领")
    assert unclaimed["total"] == 1
    none = _catalog_list(client, source_id=source["id"], owner="不存在")
    assert none["total"] == 0

    huge = _catalog_list(client, page_size=500)
    assert huge["page_size"] == 100

    for params in ({"status": "bogus"}, {"sensitivity_level": "bogus"}):
        response = client.get(
            "/api/catalog/tables", params=params, headers=headers("admin")
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "VALIDATION_FAILED"

    for params in ({"page": 0}, {"page_size": 0}):
        response = client.get(
            "/api/catalog/tables", params=params, headers=headers("admin")
        )
        assert response.status_code == 422

    missing = client.get("/api/catalog/tables/999999", headers=headers("admin"))
    assert missing.status_code == 404
    assert missing.json()["code"] == "CATALOG_TABLE_NOT_FOUND"


def test_schema_diff_marks_new_and_missing_columns(client) -> None:
    source = upload_csv(client, [["id", "name"], ["1", "a"], ["2", "b"]])
    sync_catalog_from_registry(source["id"])
    table_id = catalog_table_id(client, source["id"], "customers")
    assert set(catalog_columns(catalog_detail(client, table_id))) == {"id", "name"}

    replace_ok(
        client,
        source["id"],
        filename="customers.csv",
        content=(
            b"id,name,amount\n1,a,10.5\n2,b,20\n"
        ),
    )
    sync_catalog_from_registry(source["id"])
    detail = catalog_detail(client, table_id)
    assert detail["status"] == "pending"
    assert catalog_columns(detail)["amount"]["status"] == "pending"
    assert set(catalog_columns(detail)) == {"id", "name", "amount"}

    replace_ok(
        client,
        source["id"],
        filename="customers.csv",
        content=b"id,name\n1,a\n2,b\n",
    )
    sync_catalog_from_registry(source["id"])
    detail = catalog_detail(client, table_id)
    columns = catalog_columns(detail)
    assert set(columns) == {"id", "name", "amount"}
    assert columns["amount"]["status"] == "missing"
    assert columns["id"]["status"] == "pending"


def test_missing_table_is_marked_and_retained(client, tmp_path: Path) -> None:
    source_path = make_sqlite_db(tmp_path / "source.db")
    source = upload_sqlite(client, source_path)
    sync_catalog_from_registry(source["id"])
    assert {item["table_name"] for item in catalog_tables(client, source["id"])} == {
        "parent",
        "child",
    }

    replacement = tmp_path / "replacement.db"
    conn = sqlite3.connect(str(replacement))
    try:
        conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO parent (id, name) VALUES (1, 'alpha')")
        conn.commit()
    finally:
        conn.close()

    replace_ok(
        client,
        source["id"],
        filename="replacement.db",
        content=sqlite_bytes(replacement),
    )
    sync_catalog_from_registry(source["id"])

    items = {item["table_name"]: item for item in catalog_tables(client, source["id"])}
    assert items["parent"]["status"] == "pending"
    assert items["child"]["status"] == "missing"
    child = catalog_detail(client, int(items["child"]["id"]))
    assert child["columns"]
    assert all(column["status"] == "missing" for column in child["columns"])


def test_manual_fields_survive_resync(client) -> None:
    source = _upload_synced_customers(client)
    table_id = catalog_table_id(client, source["id"], "customers")
    detail = catalog_detail(client, table_id)
    column_id = int(catalog_columns(detail)["name"]["id"])

    updated = ok_data(
        client.patch(
            f"/api/catalog/tables/{table_id}",
            json={
                "display_name": "客户明细",
                "description": "客户手机号与金额",
                "owner": "财务共享中心",
                "business_domain": "客户域",
                "sensitivity_level": "internal",
            },
            headers=headers("analyst"),
        )
    )
    assert updated["display_name"] == "客户明细"
    assert updated["owner"] == "财务共享中心"
    assert updated["business_domain"] == "客户域"
    assert updated["status"] == "active"
    assert updated["updated_by"] == "analyst"

    column = ok_data(
        client.patch(
            f"/api/catalog/columns/{column_id}",
            json={"display_name": "姓名", "semantic_type": "text"},
            headers=headers("analyst"),
        )
    )
    assert column["display_name"] == "姓名"
    assert column["status"] == "active"
    assert column["updated_by"] == "analyst"

    replace_ok(
        client,
        source["id"],
        filename="customers.csv",
        content=(
            b"id,name,phone,email,amount\n"
            b"1,Alice,13812345678,alice@example.com,10.5\n"
            b"2,Bob,13700001111,bob@example.com,20\n"
            b"3,Carol,13600002222,carol@example.com,30\n"
        ),
    )
    run_sync_now(client, source["id"])

    after = catalog_detail(client, table_id)
    assert after["display_name"] == "客户明细"
    assert after["description"] == "客户手机号与金额"
    assert after["owner"] == "财务共享中心"
    assert after["business_domain"] == "客户域"
    assert after["updated_by"] == "analyst"
    assert after["status"] == "active"
    assert after["row_count"] == 3
    column_after = catalog_columns(after)["name"]
    assert column_after["display_name"] == "姓名"
    assert column_after["semantic_type"] == "text"
    assert column_after["updated_by"] == "analyst"
    assert column_after["status"] == "active"


def test_change_log_and_edit_permissions(client) -> None:
    source = _upload_synced_customers(client)
    table_id = catalog_table_id(client, source["id"], "customers")
    detail = catalog_detail(client, table_id)
    column_id = int(catalog_columns(detail)["name"]["id"])

    ok_data(
        client.patch(
            f"/api/catalog/tables/{table_id}",
            json={"owner": "财务共享中心"},
            headers=headers("analyst"),
        )
    )
    changes = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/changes", headers=headers("admin")
        )
    )
    assert changes["total"] == 1
    entry = changes["items"][0]
    assert entry["entity_type"] == "table"
    assert entry["entity_id"] == table_id
    assert entry["field_name"] == "owner"
    assert entry["old_value"] == "待认领"
    assert entry["new_value"] == "财务共享中心"
    assert entry["changed_by_role"] == "analyst"

    # 相同值不产生新日志。
    ok_data(
        client.patch(
            f"/api/catalog/tables/{table_id}",
            json={"owner": "财务共享中心"},
            headers=headers("analyst"),
        )
    )
    assert ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/changes", headers=headers("admin")
        )
    )["total"] == 1

    ok_data(
        client.patch(
            f"/api/catalog/columns/{column_id}",
            json={"description": "客户姓名", "semantic_type": "text"},
            headers=headers("admin"),
        )
    )
    changes = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/changes", headers=headers("admin")
        )
    )
    assert changes["total"] == 3
    column_changes = [
        entry for entry in changes["items"] if entry["entity_type"] == "column"
    ]
    assert {entry["field_name"] for entry in column_changes} == {
        "description",
        "semantic_type",
    }
    assert all(entry["column_id"] == column_id for entry in column_changes)
    assert all(entry["changed_by_role"] == "admin" for entry in column_changes)

    for path, payload in (
        (f"/api/catalog/tables/{table_id}", {"owner": "x"}),
        (f"/api/catalog/columns/{column_id}", {"description": "x"}),
    ):
        viewer = client.patch(path, json=payload, headers=headers("viewer"))
        assert viewer.status_code == 403
        assert viewer.json()["code"] == "FORBIDDEN"

    analyst_watermark = client.patch(
        f"/api/catalog/tables/{table_id}",
        json={"watermark_column": "id"},
        headers=headers("analyst"),
    )
    assert analyst_watermark.status_code == 403

    set_staging_watermark(source["id"], "customers", "old-wm")
    admin_watermark = ok_data(
        client.patch(
            f"/api/catalog/tables/{table_id}",
            json={"watermark_column": "id"},
            headers=headers("admin"),
        )
    )
    assert admin_watermark["watermark_column"] == "id"
    assert staging_row(source["id"], "customers")["watermark_value"] is None

    invalid_watermark = client.patch(
        f"/api/catalog/tables/{table_id}",
        json={"watermark_column": "not_a_column"},
        headers=headers("admin"),
    )
    assert invalid_watermark.status_code == 422
    assert invalid_watermark.json()["code"] == "VALIDATION_FAILED"

    for path in (
        f"/api/catalog/tables/{table_id}",
        f"/api/catalog/columns/{column_id}",
    ):
        unknown_field = client.patch(
            path, json={"bogus_field": "x"}, headers=headers("admin")
        )
        assert unknown_field.status_code == 422
        payload = unknown_field.json()
        assert payload["code"] == "VALIDATION_FAILED"
        assert payload["details"]["errors"][0]["type"] == "extra_forbidden"

    too_long = client.patch(
        f"/api/catalog/tables/{table_id}",
        json={"display_name": "x" * 81},
        headers=headers("admin"),
    )
    assert too_long.status_code == 422

    bad_limit = client.get(
        f"/api/catalog/tables/{table_id}/changes",
        params={"limit": 101},
        headers=headers("admin"),
    )
    assert bad_limit.status_code == 422
    assert bad_limit.json()["code"] == "VALIDATION_FAILED"

    missing_table = client.get(
        "/api/catalog/tables/999999/changes", headers=headers("admin")
    )
    assert missing_table.status_code == 404
    missing_column = client.patch(
        "/api/catalog/columns/999999", json={"description": "x"}, headers=headers("admin")
    )
    assert missing_column.status_code == 404
    assert missing_column.json()["code"] == "CATALOG_COLUMN_NOT_FOUND"


def test_preview_masks_by_role_and_validates_limits(client) -> None:
    source = _upload_synced_customers(client)
    table_id = catalog_table_id(client, source["id"], "customers")

    viewer = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/preview", headers=headers("viewer")
        )
    )
    assert viewer["columns"] == ["id", "name", "phone", "email", "amount"]
    assert len(viewer["rows"]) == 2
    assert viewer["rows"][0]["phone"] == "***"
    assert viewer["rows"][0]["email"] == "***"
    assert viewer["rows"][0]["name"] == "Alice"
    assert viewer["rows"][0]["amount"] == 10.5
    assert set(viewer["masked_columns"]) == {"phone", "email"}
    assert set(viewer["sensitive_columns"]) == {"phone", "email"}

    analyst = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/preview", headers=headers("analyst")
        )
    )
    assert analyst["rows"][0]["phone"] == "138****5678"
    assert analyst["rows"][0]["email"] == "a***@example.com"

    admin = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/preview", headers=headers("admin")
        )
    )
    assert admin["rows"][0]["phone"] == 13812345678
    assert admin["rows"][0]["email"] == "alice@example.com"
    assert set(admin["masked_columns"]) == {"phone", "email"}

    limited = ok_data(
        client.get(
            f"/api/catalog/tables/{table_id}/preview",
            params={"limit": 2},
            headers=headers("admin"),
        )
    )
    assert limited["limit"] == 2
    assert len(limited["rows"]) == 2

    for limit in (0, 21):
        response = client.get(
            f"/api/catalog/tables/{table_id}/preview",
            params={"limit": limit},
            headers=headers("admin"),
        )
        assert response.status_code == 422

    builtin_id = int(catalog_tables(client, "sqlite-local")[0]["id"])
    missing_staging = client.get(
        f"/api/catalog/tables/{builtin_id}/preview", headers=headers("admin")
    )
    assert missing_staging.status_code == 409
    assert missing_staging.json()["code"] == "CATALOG_STAGING_MISSING"

    unknown = client.get(
        "/api/catalog/tables/999999/preview", headers=headers("admin")
    )
    assert unknown.status_code == 404


def test_preview_handles_weird_identifiers_and_blob(client, tmp_path: Path) -> None:
    weird = upload_csv(
        client,
        [
            ["金额(元)", "order-id", "客户 名称"],
            ["10.5", "A-1", "张三"],
            ["20", "B-2", "李四"],
        ],
        filename="weird.csv",
        name="特殊列名源",
    )
    run_sync_now(client, weird["id"])
    weird_id = catalog_table_id(client, weird["id"], "weird")
    preview = ok_data(
        client.get(
            f"/api/catalog/tables/{weird_id}/preview", headers=headers("admin")
        )
    )
    assert preview["columns"] == ["金额(元)", "order-id", "客户 名称"]
    assert preview["rows"][0] == {
        "金额(元)": 10.5,
        "order-id": "A-1",
        "客户 名称": "张三",
    }

    blob_db = tmp_path / "docs.db"
    conn = sqlite3.connect(str(blob_db))
    try:
        conn.execute(
            'CREATE TABLE docs (id INTEGER PRIMARY KEY, payload BLOB, "金额(元)" REAL)'
        )
        conn.execute(
            'INSERT INTO docs (id, payload, "金额(元)") VALUES (1, ?, 10.5)',
            (b"\x00\x01",),
        )
        conn.commit()
    finally:
        conn.close()
    blob_source = upload_sqlite(client, blob_db, name="BLOB源")
    run_sync_now(client, blob_source["id"])
    blob_id = catalog_table_id(client, blob_source["id"], "docs")
    blob_preview = ok_data(
        client.get(
            f"/api/catalog/tables/{blob_id}/preview", headers=headers("admin")
        )
    )
    assert blob_preview["rows"] == [
        {"id": 1, "payload": "0x0001", "金额(元)": 10.5}
    ]


def test_summary_counts_filters_and_last_change(client) -> None:
    source = _upload_synced_customers(client)
    table_id = catalog_table_id(client, source["id"], "customers")

    summary = ok_data(
        client.get("/api/catalog/summary", headers=headers("admin"))
    )
    assert summary["tables_total"] == 11
    assert summary["tables_pending"] == 11
    assert summary["tables_unclaimed"] == 11
    assert summary["sources_total"] == 1
    assert summary["sources_synced"] == 1
    assert summary["last_sync_at"]
    assert summary["last_catalog_change_at"] is None

    ok_data(
        client.patch(
            f"/api/catalog/tables/{table_id}",
            json={"owner": "财务共享中心", "business_domain": "客户域"},
            headers=headers("analyst"),
        )
    )
    after = ok_data(
        client.get("/api/catalog/summary", headers=headers("admin"))
    )
    assert after["tables_pending"] == 10
    assert after["tables_unclaimed"] == 10
    assert after["last_catalog_change_at"]

    by_owner = _catalog_list(client, owner="财务共享中心")
    assert by_owner["total"] == 1
    by_domain = _catalog_list(client, business_domain="客户域")
    assert by_domain["total"] == 1
    assert by_domain["items"][0]["id"] == table_id
