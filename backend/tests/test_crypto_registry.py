"""注册库与凭据加密的单元测试（PT-01 风险点）。"""

from __future__ import annotations

import stat

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret, sanitize_error
from app.db.connection import seed_database
from app.db.registry import (
    delete_source_row,
    get_source_row,
    insert_source_row,
    list_source_rows,
    update_source_row,
)
from app.schemas.common import AppError


def _insert(source_id: str = "src_test0001", **overrides) -> dict:
    row = {
        "id": source_id,
        "name": "测试源",
        "kind": "postgresql",
        "config_json": '{"host": "db.internal"}',
        "secret_ciphertext": "token",
        "status": "new",
        "created_at": "2026-11-22T10:00:00+08:00",
        "updated_at": "2026-11-22T10:00:00+08:00",
    }
    row.update(overrides)
    insert_source_row(row)
    return row


def test_encrypt_decrypt_roundtrip_and_key_file_mode() -> None:
    plaintext = "roundtrip-p@ss-中文"
    token = encrypt_secret(plaintext)
    assert token.startswith("gAAAA")
    assert token != plaintext
    assert decrypt_secret(token) == plaintext

    key_path = settings.source_key_path
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_configured_source_secret_key_is_used_without_key_file() -> None:
    settings.source_secret_key = Fernet.generate_key().decode()
    token = encrypt_secret("configured-key-secret")
    assert decrypt_secret(token) == "configured-key-secret"
    assert not settings.source_key_path.exists()


def test_decrypt_invalid_token_returns_contract_error() -> None:
    with pytest.raises(AppError) as excinfo:
        decrypt_secret("not-a-valid-fernet-token")
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "SOURCE_SECRET_INVALID"
    assert "not-a-valid-fernet-token" not in str(excinfo.value)


def test_sanitize_error_masks_secret_and_password_fragments() -> None:
    secret = "test-pass-abc123"
    message = f"auth failed passwd={secret} host=db; pwd=other; secret={secret}"
    cleaned = sanitize_error(message, [secret])
    assert secret not in cleaned
    assert "passwd=***" in cleaned
    assert "pwd=***" in cleaned
    assert sanitize_error("x" * 500) == "x" * 300


def test_registry_crud_and_update_whitelist() -> None:
    _insert()
    row = get_source_row("src_test0001")
    assert row is not None
    assert row["name"] == "测试源"
    assert row["config_json"] == '{"host": "db.internal"}'

    update_source_row(
        "src_test0001",
        name="改名源",
        status="ready",
        created_at="1999-01-01T00:00:00+08:00",
        bogus_column="must-be-ignored",
    )
    updated = get_source_row("src_test0001")
    assert updated is not None
    assert updated["name"] == "改名源"
    assert updated["status"] == "ready"
    # created_at 属于不可变列，bogus_column 不在白名单，二者都不能被写入。
    assert updated["created_at"] == "2026-11-22T10:00:00+08:00"
    assert "bogus_column" not in updated

    assert [item["id"] for item in list_source_rows()] == ["src_test0001"]
    assert delete_source_row("src_test0001") is True
    assert delete_source_row("src_test0001") is False
    assert get_source_row("src_test0001") is None


def test_registry_insert_defaults_and_seed_isolation() -> None:
    insert_source_row({"id": "src_minimal1", "name": "最小源", "kind": "mysql"})
    row = get_source_row("src_minimal1")
    assert row is not None
    assert row["config_json"] == "{}"
    assert row["status"] == "new"
    assert row["table_count"] == 0
    assert row["size_bytes"] == 0
    assert row["created_at"] and row["updated_at"]

    # reset 播种只操作业务库，注册行必须保留。
    result = seed_database(reset=True)
    assert result["seeded"] is True
    assert get_source_row("src_minimal1") is not None
