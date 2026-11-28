"""TASK-04 测试夹具。

所有测试使用独立临时 SQLite（`tmp_path`），绝不读写演示库 `data/finance.db`。
每个测试结束后恢复全局 settings，避免测试之间互相污染。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.connection import seed_database
from app.main import app
from tests.knowledge_support import (  # noqa: F401 - 必须导入以注册知识包夹具
    broken_pack_client,
    empty_pack_client,
    knowledge_env,
    pack_client,
    pack_dir,
)
from tests.support import headers


_ISOLATED_SETTINGS_DEFAULTS: dict[str, object] = {
    "database_path": "",
    "openai_base_url": None,
    "openai_api_key": None,
    "openai_model": None,
    # 多数据源接入：注册库、上传目录、密钥与配额全部留在 tmp_path，避免污染 data/。
    "sources_database_path": "",
    "upload_dir": "",
    "source_secret_key": "",
    "source_allowed_hosts": "",
    "source_metadata_ttl_seconds": 300,
    "source_max_tables": 200,
    "source_max_columns": 500,
    "source_max_metadata_bytes": 2_000_000,
    "source_max_csv_bytes": 52_428_800,
    "source_max_excel_bytes": 20_971_520,
    "source_max_sqlite_bytes": 104_857_600,
    "source_sample_limit": 20,
    # 同步/staging：库路径与重试策略全部隔离到 tmp_path，避免污染 data/staging.db。
    "staging_database_path": "",
    "staging_batch_size": 1000,
    "staging_max_rows_per_table": 200_000,
    "staging_max_bytes_per_table": 67_108_864,
    "staging_max_retries": 3,
    "staging_retry_backoff_seconds": 1.0,
    "staging_run_stale_seconds": 300,
    "staging_run_retention_days": 180,
    "staging_max_batch_sources": 50,
    "sync_service_token": "",
    # 外部 auto-ontology 绑定：白名单与探测限制固定，避免读取开发者本机 .env。
    "auto_ontology_allowed_hosts": "",
    "auto_ontology_max_timeout_seconds": 5.0,
    "auto_ontology_max_response_bytes": 65_536,
}


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path):
    """把业务库、注册库、上传目录、密钥与配额隔离到每个测试的临时目录。"""
    original = {
        key: getattr(settings, key) for key in _ISOLATED_SETTINGS_DEFAULTS
    }
    for key, value in _ISOLATED_SETTINGS_DEFAULTS.items():
        setattr(settings, key, value)
    settings.database_path = str(tmp_path / "finance_test.db")
    settings.sources_database_path = str(tmp_path / "sources.db")
    settings.upload_dir = str(tmp_path / "uploads")
    try:
        yield
    finally:
        for key, value in original.items():
            setattr(settings, key, value)


@pytest.fixture
def db_path() -> Path:
    """当前测试使用的临时数据库路径（默认不存在）。"""
    return Path(settings.database_path)


@pytest.fixture
def seeded_db(db_path: Path) -> dict:
    """建 16 表并播种固定种子数据；返回播种结果。"""
    result = seed_database(reset=True)
    assert result["seeded"] is True
    assert result["reconciliation"]["passed"] is True
    return result


@pytest.fixture
def client(seeded_db: dict) -> TestClient:
    """已播种数据库上的 FastAPI TestClient。"""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def empty_client(db_path: Path) -> TestClient:
    """数据库文件不存在的 TestClient，用于 503/degraded 契约。"""
    assert not db_path.exists()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def ontology_client(seeded_db: dict) -> TestClient:
    """已播种且已生成 27 术语 / 16 关系 / 20 SQL 属性的 TestClient。"""
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/ontology/generate",
            json={"force": False},
            headers=headers("admin"),
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == "generated", response.text
        yield test_client


@pytest.fixture
def lenient_client(seeded_db: dict) -> TestClient:
    """不向上抛出服务端异常的 TestClient，用于故障注入后读取 500 错误信封。"""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
