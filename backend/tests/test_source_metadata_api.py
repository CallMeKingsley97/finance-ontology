"""数据源元数据 API：表清单/单表详情/外部脱敏/内置源回归/旧端点。"""

from __future__ import annotations

import re
from pathlib import Path

from tests.source_support import csv_bytes, upload_ok
from tests.support import error_data, headers, ok_data

MASK_ROWS = [
    [
        "id",
        "name",
        "phone",
        "mobile",
        "bank_account",
        "id_card",
        "email",
    ],
    [
        "1",
        "Alice",
        "13812345678",
        "13987654321",
        "6222021234567890",
        "110101199001011234",
        "alice@example.com",
    ],
    [
        "2",
        "Bob",
        "13700001111",
        "13600002222",
        "6222029876543210",
        "110101199202022345",
        "bob@example.com",
    ],
]

MASKED_COLUMNS = {"phone", "mobile", "bank_account", "id_card", "email"}


def _upload_masking_source(client) -> dict:
    return upload_ok(
        client,
        filename="customers.csv",
        content=csv_bytes(MASK_ROWS),
        kind="csv",
        name="客户敏感数据",
    )


def _detail(client, source_id: str, table: str, role: str) -> dict:
    return ok_data(
        client.get(
            f"/api/sources/{source_id}/metadata/table",
            params={"table": table, "limit": 5},
            headers=headers(role),
        )
    )


def _columns_by_name(detail: dict) -> dict[str, dict]:
    return {column["name"]: column for column in detail["columns"]}


def test_external_source_metadata_lists_sensitive_columns(client) -> None:
    source = _upload_masking_source(client)
    metadata = ok_data(client.get(f"/api/sources/{source['id']}/metadata"))

    assert metadata["source_id"] == source["id"]
    assert metadata["kind"] == "csv"
    assert metadata["cached"] is True
    assert metadata["truncated"] is False
    assert len(metadata["tables"]) == 1
    table = metadata["tables"][0]
    assert table["name"] == "customers"
    assert table["column_count"] == 7
    assert table["sensitive_columns"] == [
        "phone",
        "mobile",
        "bank_account",
        "id_card",
        "email",
    ]


def test_external_source_viewer_samples_fully_masked(client) -> None:
    source = _upload_masking_source(client)
    detail = _detail(client, source["id"], "customers", "viewer")

    assert set(detail["masked_columns"]) == MASKED_COLUMNS
    assert detail["sample_rows"]
    for row in detail["sample_rows"]:
        for column in MASKED_COLUMNS:
            assert row[column] == "***", row
    columns = _columns_by_name(detail)
    assert columns["phone"]["is_sensitive"] is True
    assert columns["phone"]["classification"] == "PII_PHONE"
    assert columns["mobile"]["classification"] == "PII_PHONE"
    assert columns["bank_account"]["classification"] == "PII_BANK_ACCOUNT"
    assert columns["id_card"]["classification"] == "PII_ID_CARD"
    assert columns["email"]["classification"] == "PII_EMAIL"
    for column in MASKED_COLUMNS:
        assert columns[column]["sample_masked"] == "***"
    assert columns["id"]["is_sensitive"] is False
    assert columns["id"]["classification"] is None


def test_external_source_analyst_samples_partially_masked(client) -> None:
    source = _upload_masking_source(client)
    detail = _detail(client, source["id"], "customers", "analyst")
    row = detail["sample_rows"][0]

    assert row["phone"] == "138****5678"
    assert row["mobile"] == "139****4321"
    assert row["bank_account"] == "**** **** 7890"
    assert row["id_card"] == "110********1234"
    assert row["email"] == "a***@example.com"
    columns = _columns_by_name(detail)
    assert columns["phone"]["sample_masked"] == "138****5678"
    assert columns["mobile"]["sample_masked"] == "139****4321"
    assert columns["bank_account"]["sample_masked"] == "**** **** 7890"
    assert columns["id_card"]["sample_masked"] == "110********1234"
    assert columns["email"]["sample_masked"] == "a***@example.com"


def test_external_source_admin_samples_return_originals(client) -> None:
    source = _upload_masking_source(client)
    detail = _detail(client, source["id"], "customers", "admin")
    row = detail["sample_rows"][0]

    assert row["phone"] == "13812345678"
    assert row["mobile"] == "13987654321"
    assert row["bank_account"] == "6222021234567890"
    assert row["id_card"] == "110101199001011234"
    assert row["email"] == "alice@example.com"
    columns = _columns_by_name(detail)
    assert columns["phone"]["sample_masked"] == "13812345678"
    assert columns["email"]["sample_masked"] == "alice@example.com"


def test_builtin_customer_masking_is_not_regressed(client) -> None:
    metadata = ok_data(client.get("/api/sources/sqlite-local/metadata"))
    assert len(metadata["tables"]) == 10
    customer = next(
        table for table in metadata["tables"] if table["name"] == "dim_customer"
    )
    assert set(customer["sensitive_columns"]) == {"phone", "bank_account"}

    viewer = _detail(client, "sqlite-local", "dim_customer", "viewer")
    assert viewer["source_id"] == "sqlite-local"
    assert viewer["schema"] == "main"
    assert viewer["table_type"] == "table"
    assert viewer["primary_key"] == ["customer_id"]
    assert set(viewer["masked_columns"]) == {"phone", "bank_account"}
    assert viewer["sample_rows"]
    for row in viewer["sample_rows"]:
        assert row["phone"] == "***"
        assert row["bank_account"] == "***"

    admin = _detail(client, "sqlite-local", "dim_customer", "admin")
    for row in admin["sample_rows"]:
        assert re.fullmatch(r"1\d{10}", str(row["phone"]))
        assert re.fullmatch(r"6222\d{15}", str(row["bank_account"]))
    columns = _columns_by_name(admin)
    assert columns["phone"]["classification"] == "PII_PHONE"
    assert columns["bank_account"]["classification"] == "PII_BANK_ACCOUNT"
    assert re.fullmatch(r"1\d{10}", str(columns["phone"]["sample_masked"]))
    assert re.fullmatch(r"6222\d{15}", str(columns["bank_account"]["sample_masked"]))


def test_metadata_errors_for_unknown_source_and_table(client) -> None:
    missing_source = client.get("/api/sources/src_missing/metadata")
    assert missing_source.status_code == 404
    assert error_data(missing_source)["code"] == "SOURCE_NOT_FOUND"

    missing_table = client.get(
        "/api/sources/src_missing/metadata/table", params={"table": "x"}
    )
    assert missing_table.status_code == 404
    assert error_data(missing_table)["code"] == "SOURCE_NOT_FOUND"

    source = _upload_masking_source(client)
    unknown_table = client.get(
        f"/api/sources/{source['id']}/metadata/table", params={"table": "nope"}
    )
    assert unknown_table.status_code == 404
    assert error_data(unknown_table)["code"] == "TABLE_NOT_FOUND"

    missing_param = client.get(f"/api/sources/{source['id']}/metadata/table")
    assert missing_param.status_code == 422
    assert error_data(missing_param)["code"] == "VALIDATION_FAILED"


def test_metadata_limit_is_validated(client) -> None:
    source = _upload_masking_source(client)
    for limit in (0, 21):
        response = client.get(
            f"/api/sources/{source['id']}/metadata/table",
            params={"table": "customers", "limit": limit},
        )
        assert response.status_code == 422
        assert error_data(response)["code"] == "VALIDATION_FAILED"


def test_legacy_source_endpoints_contract(client, tmp_path: Path) -> None:
    data = ok_data(client.post("/api/sources/test", json={}, headers=headers("admin")))
    assert data["success"] is True
    assert data["table_count"] == 16
    assert data["latest_period"] == "2026-10"
    assert data["sqlite_version"]

    missing = client.post(
        "/api/sources/test",
        json={"path": str(tmp_path / "missing.db")},
        headers=headers("admin"),
    )
    assert missing.status_code == 422
    assert error_data(missing)["code"] == "TEST_FAILED"

    outside = tmp_path / "outside.db"
    outside.write_bytes(b"not-allowed")
    not_allowed = client.post(
        "/api/sources/test",
        json={"path": str(outside)},
        headers=headers("admin"),
    )
    assert not_allowed.status_code == 422
    assert error_data(not_allowed)["code"] == "PATH_NOT_ALLOWED"

    conflict = client.post(
        "/api/sources/seed", json={"reset": False}, headers=headers("admin")
    )
    assert conflict.status_code == 409
    assert error_data(conflict)["code"] == "SOURCE_ALREADY_SEEDED"

    forbidden = client.post(
        "/api/sources/test", json={}, headers=headers("analyst")
    )
    assert forbidden.status_code == 403
    assert error_data(forbidden)["code"] == "FORBIDDEN"
