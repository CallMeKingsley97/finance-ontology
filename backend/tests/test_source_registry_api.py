"""数据源注册库 API：列表/创建/详情/删除/权限/播种隔离/CR-001 回归。"""

from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.db.registry import get_source_row, update_source_row
from tests.source_support import (
    LEAK_MARKER,
    assert_no_secret_fields,
    csv_bytes,
    upload_file,
    upload_ok,
)
from tests.support import error_data, headers, ok_data


PG_SECRET = "pg-secret-value"


def _pg_payload(**overrides) -> dict:
    payload = {
        "name": "生产 PostgreSQL",
        "kind": "postgresql",
        "config": {
            "host": "pg.internal",
            "port": 5432,
            "database": "finance",
            "username": "readonly",
            "schema": "public",
        },
        "secret": PG_SECRET,
    }
    payload.update(overrides)
    return payload


def test_sources_list_builtin_first_and_no_secret_fields(client) -> None:
    response = client.get("/api/sources")
    data = ok_data(response)
    assert data["items"]
    first = data["items"][0]
    assert first["id"] == "sqlite-local"
    assert first["kind"] == "sqlite"
    assert first["status"] == "ready"
    assert first["table_count"] == 16
    assert first["managed"] is False
    assert first["has_secret"] is False
    assert_no_secret_fields(data)
    assert "secret_ciphertext" not in response.text
    assert '"storage_path"' not in response.text


def test_create_postgres_source_and_secret_not_echoed(client) -> None:
    response = client.post(
        "/api/sources", json=_pg_payload(), headers=headers("admin")
    )
    data = ok_data(response)
    assert data["kind"] == "postgresql"
    assert data["status"] == "new"
    assert data["managed"] is True
    assert data["has_secret"] is True
    assert data["config"]["host"] == "pg.internal"
    assert data["config"]["schema"] == "public"
    assert_no_secret_fields(data)
    assert PG_SECRET not in response.text

    listed = ok_data(client.get("/api/sources"))["items"]
    assert [item["id"] for item in listed][0] == "sqlite-local"
    assert any(item["id"] == data["id"] for item in listed)

    row = get_source_row(data["id"])
    assert row is not None
    assert row["secret_ciphertext"]
    assert row["secret_ciphertext"] != PG_SECRET
    assert decrypt_secret(row["secret_ciphertext"]) == PG_SECRET


def test_create_mysql_defaults_schema_to_database(client) -> None:
    payload = _pg_payload(
        name="生产 MySQL",
        kind="mysql",
        config={
            "host": "mysql.internal",
            "port": 3306,
            "database": "finance_mysql",
            "username": "readonly",
        },
        secret="mysql-secret",
    )
    data = ok_data(client.post("/api/sources", json=payload, headers=headers("admin")))
    assert data["config"]["schema"] == "finance_mysql"


def test_blocked_and_allowlisted_hosts(client) -> None:
    blocked = client.post(
        "/api/sources",
        json=_pg_payload(
            config={
                "host": "169.254.169.254",
                "port": 5432,
                "database": "finance",
                "username": "readonly",
            }
        ),
        headers=headers("admin"),
    )
    assert blocked.status_code == 422
    assert error_data(blocked)["code"] == "VALIDATION_FAILED"

    settings.source_allowed_hosts = "pg.internal"
    not_allowed = client.post(
        "/api/sources",
        json=_pg_payload(
            config={
                "host": "other.internal",
                "port": 5432,
                "database": "finance",
                "username": "readonly",
            }
        ),
        headers=headers("admin"),
    )
    assert not_allowed.status_code == 422
    assert error_data(not_allowed)["code"] == "VALIDATION_FAILED"

    assert ok_data(
        client.post("/api/sources", json=_pg_payload(), headers=headers("admin"))
    )["config"]["host"] == "pg.internal"


def test_source_detail_builtin_and_unknown(client) -> None:
    builtin = ok_data(client.get("/api/sources/sqlite-local"))
    assert builtin["id"] == "sqlite-local"
    assert builtin["managed"] is False
    assert builtin["table_count"] == 16

    missing = client.get("/api/sources/src_missing")
    assert missing.status_code == 404
    assert error_data(missing)["code"] == "SOURCE_NOT_FOUND"


def test_delete_registered_source_and_builtin_conflict(client) -> None:
    source = ok_data(
        client.post("/api/sources", json=_pg_payload(), headers=headers("admin"))
    )

    deleted = ok_data(
        client.delete(f"/api/sources/{source['id']}", headers=headers("admin"))
    )
    assert deleted == {"deleted": True, "id": source["id"]}
    assert all(
        item["id"] != source["id"]
        for item in ok_data(client.get("/api/sources"))["items"]
    )
    assert client.get(f"/api/sources/{source['id']}").status_code == 404

    builtin = client.delete("/api/sources/sqlite-local", headers=headers("admin"))
    assert builtin.status_code == 409
    assert error_data(builtin)["code"] == "BUILTIN_SOURCE"


def test_analyst_cannot_create_test_delete_or_upload(client) -> None:
    source = ok_data(
        client.post("/api/sources", json=_pg_payload(), headers=headers("admin"))
    )
    forbidden_create = client.post(
        "/api/sources", json=_pg_payload(), headers=headers("analyst")
    )
    assert forbidden_create.status_code == 403
    assert error_data(forbidden_create)["code"] == "FORBIDDEN"

    forbidden_test = client.post(
        f"/api/sources/{source['id']}/test", headers=headers("analyst")
    )
    assert forbidden_test.status_code == 403
    assert error_data(forbidden_test)["code"] == "FORBIDDEN"

    forbidden_delete = client.delete(
        f"/api/sources/{source['id']}", headers=headers("analyst")
    )
    assert forbidden_delete.status_code == 403
    assert error_data(forbidden_delete)["code"] == "FORBIDDEN"

    forbidden_upload = upload_file(
        client,
        filename="analyst.csv",
        content=csv_bytes([["id", "name"], ["1", "x"]]),
        kind="csv",
        role="analyst",
    )
    assert forbidden_upload.status_code == 403
    assert error_data(forbidden_upload)["code"] == "FORBIDDEN"


def test_seed_reset_keeps_registered_sources_and_uploads(client) -> None:
    source = upload_ok(
        client,
        filename="kept.csv",
        content=csv_bytes([["id", "name"], ["1", "保留"]]),
        kind="csv",
        name="保留源",
    )
    row = get_source_row(source["id"])
    assert row is not None
    stored = settings.uploads_dir / source["id"]
    assert stored.exists()

    seeded = ok_data(
        client.post("/api/sources/seed", json={"reset": True}, headers=headers("admin"))
    )
    assert seeded["seeded"] is True

    assert settings.sources_db_path.exists()
    assert stored.exists()
    kept = ok_data(client.get(f"/api/sources/{source['id']}"))
    assert kept["id"] == source["id"]
    assert kept["name"] == "保留源"
    assert kept["kind"] == "csv"


@pytest.mark.parametrize(
    ("payload", "expected_loc"),
    [
        (
            {
                "kind": "postgresql",
                "config": {
                    "host": "pg.internal",
                    "port": 5432,
                    "database": "finance",
                    "username": "readonly",
                },
                "secret": LEAK_MARKER,
            },
            ["name"],
        ),
        (
            {
                "name": "超长密码",
                "kind": "postgresql",
                "config": {
                    "host": "pg.internal",
                    "port": 5432,
                    "database": "finance",
                    "username": "readonly",
                },
                "secret": LEAK_MARKER + "S" * 600,
            },
            ["secret"],
        ),
        (
            {
                "name": "未知配置字段",
                "kind": "postgresql",
                "config": {
                    "host": "pg.internal",
                    "port": 5432,
                    "database": "finance",
                    "username": "readonly",
                    "extra_secret_field": LEAK_MARKER,
                },
                "secret": "ok-secret",
            },
            ["config", "extra_secret_field"],
        ),
    ],
)
def test_create_validation_errors_do_not_echo_secret(
    client, payload: dict, expected_loc: list[str]
) -> None:
    response = client.post("/api/sources", json=payload, headers=headers("admin"))
    assert response.status_code == 422
    body = error_data(response)
    assert body["code"] == "VALIDATION_FAILED"
    assert LEAK_MARKER not in response.text
    errors = body["details"]["errors"]
    assert errors
    for item in errors:
        # CR-001 回归：只能出现 type/loc/msg，禁止 input/ctx/url 回显请求体。
        assert set(item) <= {"type", "loc", "msg"}, item
    assert any(item["loc"] == expected_loc for item in errors), errors


@pytest.mark.parametrize("body", ["{not-json", ""])
def test_create_invalid_json_returns_envelope_without_input(client, body: str) -> None:
    response = client.post(
        "/api/sources",
        content=body,
        headers={"Content-Type": "application/json", "X-Demo-Role": "admin"},
    )
    assert response.status_code == 422
    payload = error_data(response)
    assert payload["code"] == "VALIDATION_FAILED"
    assert "input" not in json.dumps(payload, ensure_ascii=False)


def test_openapi_keeps_source_create_request_body(client) -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/sources"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["title"] == "SourceCreateRequest"
    serialized = json.dumps(schema, ensure_ascii=False)
    assert "secret_ciphertext" not in serialized
    assert "storage_path" not in serialized


def test_invalid_fernet_token_keeps_contract_error_on_test_and_metadata(client) -> None:
    source = ok_data(
        client.post("/api/sources", json=_pg_payload(), headers=headers("admin"))
    )
    update_source_row(
        source["id"],
        secret_ciphertext="not-a-valid-fernet-token",
        status="new",
    )

    tested = client.post(
        f"/api/sources/{source['id']}/test", headers=headers("admin")
    )
    assert tested.status_code == 422
    body = error_data(tested)
    assert body["code"] == "SOURCE_SECRET_INVALID"
    assert "not-a-valid-fernet-token" not in tested.text

    row = get_source_row(source["id"])
    assert row is not None
    assert row["status"] == "failed"
    assert row["last_error"]
    assert "not-a-valid-fernet-token" not in row["last_error"]

    metadata = client.get(f"/api/sources/{source['id']}/metadata")
    assert metadata.status_code == 422
    assert error_data(metadata)["code"] == "SOURCE_SECRET_INVALID"

    # 详情不触发解密，凭据损坏的源仍可被查看/删除。
    assert ok_data(client.get(f"/api/sources/{source['id']}"))["has_secret"] is True
